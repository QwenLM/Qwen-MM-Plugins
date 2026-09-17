"""Programmatic HTML/PDF rendering and PDF page rasterization."""

from __future__ import annotations

import base64
import hashlib
import html
import mimetypes
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from shared.syscmd import find_tool

from .schemas import DocumentDraft, SelectionResult, StepSelection
from .state import atomic_write_json


@dataclass(frozen=True)
class RenderArtifacts:
    """Paths and renderer metadata for one complete render."""

    html_path: Path
    layout_path: Path
    pdf_path: Path | None
    renderer: str
    page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "html_path": str(self.html_path),
            "layout_path": str(self.layout_path),
            "pdf_path": str(self.pdf_path) if self.pdf_path else None,
            "renderer": self.renderer,
            "page_count": self.page_count,
        }


_CJK_RANGES = (
    (0x2E80, 0x2FFF),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
)
_COMMON_CJK_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)
_COMMON_UNICODE_FONTS = (
    *_COMMON_CJK_FONTS,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
)
_COMMON_BOLD_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
)


def _has_cjk(text: str) -> bool:
    return any(start <= ord(character) <= end for character in text for start, end in _CJK_RANGES)


def _section_labels(language: str) -> dict[str, str]:
    if language.lower().startswith("zh"):
        return {"prerequisites": "前置条件", "tools": "所需工具", "safety": "安全提示", "mistakes": "常见错误"}
    return {"prerequisites": "Prerequisites", "tools": "Tools", "safety": "Safety", "mistakes": "Common mistakes"}


def _all_text(draft: DocumentDraft) -> str:
    values = [
        draft.title,
        draft.overview,
        *draft.prerequisites,
        *draft.tools,
        *draft.safety,
        *draft.common_mistakes,
        draft.closing,
    ]
    for step in draft.steps:
        values.extend((step.title, step.instruction, step.caption, *step.details))
    return "\n".join(values)


def _resolve_fonts(
    text: str,
    font: str | Path | None,
    bold_font: str | Path | None,
) -> tuple[Path | None, Path | None]:
    regular = Path(font).expanduser().resolve() if font else None
    bold = Path(bold_font).expanduser().resolve() if bold_font else None
    if regular is not None and not regular.is_file():
        raise FileNotFoundError(f"font does not exist: {regular}")
    if bold is not None and not bold.is_file():
        raise FileNotFoundError(f"bold font does not exist: {bold}")
    cjk = _has_cjk(text)
    if regular is None:
        candidates = _COMMON_CJK_FONTS if cjk else _COMMON_UNICODE_FONTS
        regular = next((Path(item) for item in candidates if Path(item).is_file()), None)
    if bold is None and not cjk:
        bold = next((Path(item) for item in _COMMON_BOLD_FONTS if Path(item).is_file()), None)
    if cjk and regular is None:
        raise RuntimeError(
            "document contains CJK text but no usable CJK font was supplied or found; pass font=<path>"
        )
    return regular, bold or regular


def _normalize_draft(value: DocumentDraft | dict[str, Any]) -> DocumentDraft:
    return value if isinstance(value, DocumentDraft) else DocumentDraft.parse(value)


def _normalize_selections(
    value: SelectionResult | list[StepSelection] | list[dict[str, Any]] | dict[str, Any],
) -> list[StepSelection]:
    if isinstance(value, SelectionResult):
        value.validate()
        return value.selections
    if isinstance(value, dict):
        value = value.get("selections", value.get("steps", []))
    if not isinstance(value, list):
        raise TypeError("selections must be SelectionResult, a list, or an object containing selections")
    return [item if isinstance(item, StepSelection) else StepSelection.parse(item) for item in value]


def _image_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_document_html(
    draft: DocumentDraft | dict[str, Any],
    selections: SelectionResult | list[StepSelection] | list[dict[str, Any]] | dict[str, Any],
    output_dir: str | Path,
    *,
    image_root: str | Path | None = None,
    input_hash: str = "",
    language: str = "zh-CN",
    font: str | Path | None = None,
    bold_font: str | Path | None = None,
    layout_options: dict[str, Any] | None = None,
) -> RenderArtifacts:
    """Build escaped HTML and layout JSON without asking a model to create markup."""
    normalized_draft = _normalize_draft(draft)
    normalized_draft.validate()
    normalized_selections = _normalize_selections(selections)
    output = Path(output_dir).expanduser().resolve()
    root = Path(image_root).expanduser().resolve() if image_root else output
    output.mkdir(parents=True, exist_ok=True)
    regular_font, resolved_bold = _resolve_fonts(_all_text(normalized_draft), font, bold_font)
    options = {"font_scale": 1.0, "image_max_height": 420, "page_break_steps": False}
    options.update(layout_options or {})
    labels = _section_labels(language)
    font_scale = max(0.7, min(1.5, float(options["font_scale"])))
    image_height = max(160, min(720, int(options["image_max_height"])))
    selection_map = {selection.step_id: selection for selection in normalized_selections}
    layout_steps: list[dict[str, Any]] = []
    sections: list[str] = []
    for step in normalized_draft.steps:
        selected = selection_map.get(step.id, StepSelection(step_id=step.id))
        images = []
        image_markup = []
        for choice in selected.choices[:2]:
            source = (root / choice.frame_path).resolve()
            if root not in source.parents or not source.is_file():
                raise FileNotFoundError(f"selected frame is missing or outside image root: {choice.frame_path}")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            caption = choice.caption or step.caption or f"{choice.timestamp:.1f}s"
            images.append(
                {
                    "target_id": choice.target_id,
                    "role": choice.role,
                    "path": choice.frame_path,
                    "timestamp": choice.timestamp,
                    "sha256": digest,
                    "caption": caption,
                }
            )
            image_markup.append(
                '<figure class="frame">'
                f'<img src="{_image_data_uri(source)}" alt="{html.escape(caption, quote=True)}">'
                f"<figcaption>{html.escape(caption)} · {choice.timestamp:.1f}s</figcaption>"
                "</figure>"
            )
        details = "".join(f"<li>{html.escape(item)}</li>" for item in step.details)
        break_class = " step-break" if options.get("page_break_steps") else ""
        sections.append(
            f'<section class="step{break_class}" data-step-id="{step.id}">'
            f"<h2><span>{step.id}</span>{html.escape(step.title)}</h2>"
            f'<p class="instruction">{html.escape(step.instruction)}</p>'
            f"<ul>{details}</ul>"
            f'<div class="frames">{"".join(image_markup)}</div>'
            "</section>"
        )
        layout_steps.append(
            {
                "id": step.id,
                "title": step.title,
                "image_required": any(choice.role == "primary" for choice in selected.choices),
                "images": images,
                "misses": dict(selected.misses),
            }
        )

    def bullet_section(title: str, items: list[str], class_name: str = "") -> str:
        if not items:
            return ""
        body = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        return f'<section class="panel {class_name}"><h2>{html.escape(title)}</h2><ul>{body}</ul></section>'

    font_face = ""
    family = "Arial, sans-serif"
    if regular_font:
        family = "VideoNote, sans-serif"
        font_face += f"@font-face{{font-family:VideoNote;src:url('{regular_font.as_uri()}');font-weight:400;}}"
    if resolved_bold:
        font_face += f"@font-face{{font-family:VideoNote;src:url('{resolved_bold.as_uri()}');font-weight:700;}}"
    document = f"""<!doctype html>
<html lang="{html.escape(language, quote=True)}"><head><meta charset="utf-8"><style>
{font_face}
@page {{ size: A4; margin: 16mm 15mm 17mm; @bottom-right {{ content: counter(page); color:#64748b; }} }}
* {{ box-sizing:border-box; }} body {{ font-family:{family}; color:#172033; font-size:{11 * font_scale:.2f}pt; line-height:1.55; margin:0; }}
h1 {{ font-size:{26 * font_scale:.2f}pt; color:#0f3d66; margin:0 0 5mm; }} h2 {{ color:#154f7d; margin:0 0 2mm; font-size:{16 * font_scale:.2f}pt; }}
.hero {{ border-bottom:2px solid #4aa3df; margin-bottom:6mm; padding-bottom:5mm; }} .overview {{ font-size:{12 * font_scale:.2f}pt; }}
.panel,.step {{ break-inside:avoid; border:1px solid #dbe7f0; border-radius:7px; padding:5mm; margin:0 0 5mm; background:#f9fcff; }}
.step-break {{ break-before:page; }} .step h2 span {{ background:#1677aa; color:white; border-radius:50%; display:inline-block; text-align:center; width:8mm; height:8mm; margin-right:3mm; }}
.instruction {{ font-weight:600; }} ul {{ padding-left:6mm; }} .frames {{ display:flex; gap:4mm; flex-wrap:wrap; }}
.frame {{ margin:2mm 0 0; flex:1 1 70mm; }} .frame img {{ display:block; max-width:100%; max-height:{image_height}px; object-fit:contain; margin:auto; border:1px solid #b9cddd; }}
figcaption {{ text-align:center; color:#526577; font-size:{9 * font_scale:.2f}pt; margin-top:1mm; }} .safety {{ border-color:#e8b36d; background:#fffaf1; }}
</style></head><body>
<header class="hero"><h1>{html.escape(normalized_draft.title)}</h1><p class="overview">{html.escape(normalized_draft.overview)}</p></header>
{bullet_section(labels['prerequisites'], normalized_draft.prerequisites)}
{bullet_section(labels['tools'], normalized_draft.tools)}
{bullet_section(labels['safety'], normalized_draft.safety, 'safety')}
{''.join(sections)}
{bullet_section(labels['mistakes'], normalized_draft.common_mistakes)}
{f'<section class="panel"><p>{html.escape(normalized_draft.closing)}</p></section>' if normalized_draft.closing else ''}
</body></html>"""
    html_path = output / "document.html"
    layout_path = output / "layout.json"
    _atomic_write_text(html_path, document)
    atomic_write_json(
        layout_path,
        {
            "schema": 1,
            "input_sha256": input_hash,
            "title": normalized_draft.title,
            "language": language,
            "step_count": len(normalized_draft.steps),
            "steps": layout_steps,
            "font": str(regular_font) if regular_font else None,
            "bold_font": str(resolved_bold) if resolved_bold else None,
            "options": options,
        },
    )
    return RenderArtifacts(html_path, layout_path, None, "html", 0)


def _reportlab_pdf(
    draft: DocumentDraft,
    selections: list[StepSelection],
    pdf_path: Path,
    image_root: Path,
    regular_font: Path | None,
    bold_font: Path | None,
    options: dict[str, Any],
    language: str,
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Image,
        KeepTogether,
        ListFlowable,
        ListItem,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    regular_name, bold_name = "Helvetica", "Helvetica-Bold"
    if regular_font:
        token = hashlib.sha1(str(regular_font).encode()).hexdigest()[:10]
        regular_name = f"VideoNote-{token}"
        try:
            pdfmetrics.registerFont(TTFont(regular_name, str(regular_font), subfontIndex=0))
        except TypeError:
            pdfmetrics.registerFont(TTFont(regular_name, str(regular_font)))
    if bold_font:
        token = hashlib.sha1(str(bold_font).encode()).hexdigest()[:10]
        bold_name = f"VideoNoteBold-{token}"
        try:
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_font), subfontIndex=0))
        except TypeError:
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_font)))
    elif regular_font:
        bold_name = regular_name
    scale = max(0.7, min(1.5, float(options.get("font_scale", 1.0))))
    styles = getSampleStyleSheet()
    title = ParagraphStyle("VNTitle", parent=styles["Title"], fontName=bold_name, fontSize=25 * scale, leading=30 * scale, textColor=colors.HexColor("#0f3d66"), spaceAfter=10 * mm)
    heading = ParagraphStyle("VNHeading", parent=styles["Heading2"], fontName=bold_name, fontSize=15 * scale, leading=19 * scale, textColor=colors.HexColor("#154f7d"), spaceBefore=4 * mm, spaceAfter=2 * mm)
    body = ParagraphStyle("VNBody", parent=styles["BodyText"], fontName=regular_name, fontSize=10.5 * scale, leading=15 * scale, textColor=colors.HexColor("#172033"), spaceAfter=2 * mm)
    caption_style = ParagraphStyle("VNCaption", parent=body, alignment=TA_CENTER, fontSize=8.5 * scale, textColor=colors.HexColor("#526577"))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = pdf_path.with_name(f".{pdf_path.name}.{uuid4().hex}.tmp")
    document = SimpleDocTemplate(str(temporary), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=16 * mm, bottomMargin=17 * mm, title=draft.title)
    story: list[Any] = [Paragraph(html.escape(draft.title), title), Paragraph(html.escape(draft.overview), body), Spacer(1, 4 * mm)]
    labels = _section_labels(language)

    def add_list(section_title: str, items: list[str]) -> None:
        if not items:
            return
        story.append(Paragraph(html.escape(section_title), heading))
        story.append(ListFlowable([ListItem(Paragraph(html.escape(item), body)) for item in items], bulletType="bullet", leftIndent=6 * mm))

    add_list(labels["prerequisites"], draft.prerequisites)
    add_list(labels["tools"], draft.tools)
    add_list(labels["safety"], draft.safety)
    selection_map = {selection.step_id: selection for selection in selections}
    for index, step in enumerate(draft.steps):
        if options.get("page_break_steps") and index:
            story.append(PageBreak())
        block: list[Any] = [Paragraph(f"{step.id}. {html.escape(step.title)}", heading), Paragraph(html.escape(step.instruction), body)]
        if step.details:
            block.append(ListFlowable([ListItem(Paragraph(html.escape(item), body)) for item in step.details], bulletType="bullet", leftIndent=6 * mm))
        selected = selection_map.get(step.id, StepSelection(step.id))
        for choice in selected.choices[:2]:
            source = (image_root / choice.frame_path).resolve()
            reader = ImageReader(str(source))
            width, height = reader.getSize()
            max_width = 170 * mm
            max_height = min(120 * mm, float(options.get("image_max_height", 420)) * 0.22 * mm)
            ratio = min(max_width / width, max_height / height)
            block.extend(
                [
                    Spacer(1, 2 * mm),
                    Image(str(source), width=width * ratio, height=height * ratio),
                    Paragraph(html.escape(choice.caption or step.caption or f"{choice.timestamp:.1f}s"), caption_style),
                ]
            )
        story.append(KeepTogether(block))
    add_list(labels["mistakes"], draft.common_mistakes)
    if draft.closing:
        story.extend([Spacer(1, 4 * mm), Paragraph(html.escape(draft.closing), body)])
    try:
        document.build(story)
        os.replace(temporary, pdf_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _pillow_pdf(
    draft: DocumentDraft,
    selections: list[StepSelection],
    pdf_path: Path,
    image_root: Path,
    regular_font: Path | None,
    bold_font: Path | None,
    options: dict[str, Any],
    language: str,
) -> None:
    """Last-resort raster PDF writer for environments missing both primary renderers."""
    from PIL import Image, ImageDraw, ImageFont

    if regular_font is None and any(ord(character) > 127 for character in _all_text(draft)):
        raise RuntimeError("non-ASCII document requires a usable Unicode font")
    width, height = 1240, 1754
    margin = 95
    scale = max(0.7, min(1.5, float(options.get("font_scale", 1.0))))

    def load_font(path: Path | None, size: int) -> Any:
        return ImageFont.truetype(str(path), size=size, index=0) if path else ImageFont.load_default(size=size)

    body_font = load_font(regular_font, round(29 * scale))
    heading_font = load_font(bold_font or regular_font, round(42 * scale))
    title_font = load_font(bold_font or regular_font, round(56 * scale))
    labels = _section_labels(language)

    def wrapped(draw: Any, text: str, font_object: Any, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for character in text:
            trial = current + character
            if current and draw.textbbox((0, 0), trial, font=font_object)[2] > max_width:
                lines.append(current)
                current = character
            else:
                current = trial
        if current:
            lines.append(current)
        return lines or [""]

    pages: list[Any] = []
    cover = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(cover)
    y = margin
    for line in wrapped(draw, draft.title, title_font, width - 2 * margin):
        draw.text((margin, y), line, fill="#0f3d66", font=title_font)
        y += round(72 * scale)
    y += 35
    for line in wrapped(draw, draft.overview, body_font, width - 2 * margin):
        draw.text((margin, y), line, fill="#172033", font=body_font)
        y += round(44 * scale)
    for label, items in (
        (labels["prerequisites"], draft.prerequisites),
        (labels["tools"], draft.tools),
        (labels["safety"], draft.safety),
    ):
        if not items:
            continue
        y += 30
        draw.text((margin, y), label, fill="#154f7d", font=heading_font)
        y += round(58 * scale)
        for item in items:
            for line in wrapped(draw, f"• {item}", body_font, width - 2 * margin):
                draw.text((margin + 20, y), line, fill="#172033", font=body_font)
                y += round(42 * scale)
    pages.append(cover)
    selection_map = {selection.step_id: selection for selection in selections}
    for step in draft.steps:
        page = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(page)
        y = margin
        for line in wrapped(draw, f"{step.id}. {step.title}", heading_font, width - 2 * margin):
            draw.text((margin, y), line, fill="#154f7d", font=heading_font)
            y += round(56 * scale)
        for text in (step.instruction, *[f"• {item}" for item in step.details]):
            for line in wrapped(draw, text, body_font, width - 2 * margin):
                draw.text((margin, y), line, fill="#172033", font=body_font)
                y += round(42 * scale)
            y += 8
        selected = selection_map.get(step.id, StepSelection(step.id))
        remaining = max(250, height - y - margin - 60)
        for choice in selected.choices[:2]:
            source = (image_root / choice.frame_path).resolve()
            with Image.open(source) as opened:
                picture = opened.convert("RGB")
                picture.thumbnail((width - 2 * margin, remaining // max(1, len(selected.choices))))
                x = (width - picture.width) // 2
                page.paste(picture, (x, y))
                y += picture.height + 12
            caption = choice.caption or step.caption or f"{choice.timestamp:.1f}s"
            draw.text((margin, y), caption, fill="#526577", font=body_font)
            y += round(46 * scale)
        pages.append(page)
    if draft.common_mistakes or draft.closing:
        page = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(page)
        y = margin
        draw.text((margin, y), labels["mistakes"], fill="#154f7d", font=heading_font)
        y += round(60 * scale)
        for text in (*[f"• {item}" for item in draft.common_mistakes], draft.closing):
            for line in wrapped(draw, text, body_font, width - 2 * margin):
                draw.text((margin, y), line, fill="#172033", font=body_font)
                y += round(44 * scale)
        pages.append(page)
    temporary = pdf_path.with_name(f".{pdf_path.name}.{uuid4().hex}.tmp")
    try:
        pages[0].save(temporary, "PDF", resolution=150.0, save_all=True, append_images=pages[1:])
        os.replace(temporary, pdf_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def pdf_page_count(pdf_path: str | Path) -> int:
    source = Path(pdf_path)
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(source))
        try:
            return len(document)
        finally:
            document.close()
    except Exception:
        try:
            from pypdf import PdfReader

            return len(PdfReader(str(source)).pages)
        except Exception:
            raw = source.read_bytes()
            count = len(__import__("re").findall(rb"/Type\s*/Page\b", raw))
            if count < 1:
                raise RuntimeError("cannot determine rendered PDF page count")
            return count


def render_document(
    draft: DocumentDraft | dict[str, Any],
    selections: SelectionResult | list[StepSelection] | list[dict[str, Any]] | dict[str, Any],
    output_dir: str | Path,
    *,
    image_root: str | Path | None = None,
    input_hash: str = "",
    language: str = "zh-CN",
    font: str | Path | None = None,
    bold_font: str | Path | None = None,
    layout_options: dict[str, Any] | None = None,
    pdf_name: str = "document.pdf",
) -> RenderArtifacts:
    """Generate document.html, layout.json, and a valid PDF using WeasyPrint or ReportLab."""
    normalized_draft = _normalize_draft(draft)
    normalized_selections = _normalize_selections(selections)
    output = Path(output_dir).expanduser().resolve()
    root = Path(image_root).expanduser().resolve() if image_root else output
    built = build_document_html(
        normalized_draft,
        normalized_selections,
        output,
        image_root=root,
        input_hash=input_hash,
        language=language,
        font=font,
        bold_font=bold_font,
        layout_options=layout_options,
    )
    pdf_path = output / pdf_name
    temporary = pdf_path.with_name(f".{pdf_path.name}.{uuid4().hex}.tmp")
    renderer = ""
    try:
        try:
            from weasyprint import HTML

            HTML(filename=str(built.html_path), base_url=str(output)).write_pdf(str(temporary))
            if not temporary.is_file() or not temporary.read_bytes().startswith(b"%PDF-"):
                raise RuntimeError("WeasyPrint produced an invalid PDF")
            os.replace(temporary, pdf_path)
            renderer = "weasyprint"
        except Exception:  # WeasyPrint is optional; any renderer failure activates the deterministic fallback.
            if temporary.exists():
                temporary.unlink()
            regular, bold = _resolve_fonts(_all_text(normalized_draft), font, bold_font)
            try:
                _reportlab_pdf(
                    normalized_draft,
                    normalized_selections,
                    pdf_path,
                    root,
                    regular,
                    bold,
                    layout_options or {},
                    language,
                )
                renderer = "reportlab"
            except ImportError:
                _pillow_pdf(
                    normalized_draft,
                    normalized_selections,
                    pdf_path,
                    root,
                    regular,
                    bold,
                    layout_options or {},
                    language,
                )
                renderer = "pillow"
    finally:
        if temporary.exists():
            temporary.unlink()
    if not pdf_path.is_file() or not pdf_path.read_bytes()[:5] == b"%PDF-":
        raise RuntimeError("PDF renderer did not produce a valid PDF")
    return RenderArtifacts(built.html_path, built.layout_path, pdf_path, renderer, pdf_page_count(pdf_path))


def rasterize_pdf(
    pdf_path: str | Path,
    output_dir: str | Path,
    *,
    dpi: int = 120,
) -> list[Path]:
    """Rasterize all pages, preferring pypdfium2 and falling back to pdftoppm."""
    source = Path(pdf_path).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    if dpi < 36 or dpi > 600:
        raise ValueError("dpi must be in [36, 600]")
    destination.mkdir(parents=True, exist_ok=True)
    for previous in destination.glob("page-*.png"):
        previous.unlink()
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(source))
        paths = []
        try:
            for index in range(len(document)):
                page = document[index]
                bitmap = page.render(scale=dpi / 72.0)
                path = destination / f"page-{index + 1:04d}.png"
                bitmap.to_pil().save(path, "PNG")
                paths.append(path)
                bitmap.close()
                page.close()
        finally:
            document.close()
        return paths
    except Exception:  # pypdfium2 is optional and malformed/unsupported PDFs may fail at runtime.
        executable = find_tool("pdftoppm")
        prefix = destination / "page"
        result = subprocess.run(
            [executable, "-png", "-r", str(dpi), str(source), str(prefix)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pdftoppm failed: {result.stderr.strip() or 'unknown error'}")
        generated = sorted(destination.glob("page-*.png"))
        renamed = []
        for index, path in enumerate(generated, 1):
            target = destination / f"page-{index:04d}.png"
            if path != target:
                shutil.move(path, target)
            renamed.append(target)
        if not renamed:
            raise RuntimeError("PDF rasterization produced no pages")
        return renamed
