"""Image safety net on IR (spec §5): scan -> extract -> VLM -> inject/fail-open/budget."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

from .cache import DescriptionCache, image_key
from .capability import CapabilityTable
from .config import ProxyConfig
from .ir import _DATA_URL_RE, ContentBlock, ImageBlock, IRRequest, Message, extract_data_urls
from .vlm import VLMError, _is_retryable

ANALYZE_DEPTH_LIMIT = 50
GOLDEN_WINDOW_DEPTH = 10
CONTEXT_SAFETY_MARGIN = 0.9
AVG_DESC_BUDGET = 100  # tokens

# spec §5.4 VLM 重试退避：首次 + VLM_MAX_ATTEMPTS-1 次重试（对齐 BATCH_MAX_ATTEMPTS=2），
# 指数退避 VLM_BACKOFF_BASE·2^(attempt) 秒 + 随机抖动，见 _backoff。
VLM_MAX_ATTEMPTS = 3
VLM_BACKOFF_BASE = 3.0
VLM_BACKOFF_JITTER = 1.0


@dataclass
class ProcessResult:
    ir: IRRequest
    stripped: int = 0
    injected: int = 0
    fail_open: str | None = None
    vlm_calls: int = 0


@dataclass
class _ImageTarget:
    """单个图片目标：结构化图片块或文本内嵌 data URL。"""

    msg: Message
    image: ImageBlock
    path: tuple[int, ...]
    data_url: str | None = None
    cleanup: list[tuple[tuple[int, ...], str]] = field(default_factory=list)
    index: int = 0
    msg_count: int = 1

    @property
    def key(self) -> str:
        return image_key(self.image)


def _blocks_at(msg: Message, path: tuple[int, ...]) -> list[ContentBlock]:
    blocks = msg.content
    for idx in path[:-1]:
        blocks = blocks[idx].tool_result_content or []
    return blocks


def _replace_text_at(msg: Message, path: tuple[int, ...], url: str, replacement: str) -> None:
    block = _blocks_at(msg, path)[path[-1]]
    if block.text:
        block.text = block.text.replace(url, replacement, 1)


class Pipeline:
    def __init__(self, vlm, cache: DescriptionCache, semaphore: threading.Semaphore | None = None):
        self.vlm = vlm
        self.cache = cache
        self.semaphore = semaphore or threading.Semaphore(5)
        self.table = CapabilityTable()

    def process(self, ir: IRRequest, cfg: ProxyConfig, harness: str | None = None) -> ProcessResult:
        if self.table.judge(ir.model, cfg, harness) == "vision":
            return ProcessResult(ir=ir)  # vision model: zero overhead passthrough
        result = ProcessResult(ir=ir)
        budget = self._budget(ir, cfg)
        if budget <= 1:
            result.stripped = self._strip_all(ir, reason="上下文已满，图片未处理")
            result.fail_open = "CONTEXT_FULL"
            return result

        current_turn_msg = self._current_turn(ir)
        current_targets: list[_ImageTarget] = []
        golden_targets: list[_ImageTarget] = []
        deep_targets: list[_ImageTarget] = []
        history_seen = 0
        for msg, targets in self._collect_images(ir):
            if msg is current_turn_msg:
                current_targets.extend(targets)
            elif history_seen < GOLDEN_WINDOW_DEPTH:
                golden_targets.extend(targets)
                history_seen += 1
            else:
                deep_targets.extend(targets)

        # 当前轮不限量：全部同步描述并注入；有用户问题则用 Tier2 聚焦（spec §5.4）
        question = self._current_question(ir)
        for target in current_targets:
            outcome = self._handle_one(target, result, question)
            if outcome == "stripped":
                result.stripped += 1

        # 黄金窗口历史：从新到旧，X 封顶 VLM 调用；缓存命中不计入预算
        quota = max(int(budget), 1)
        for target in golden_targets:
            if self.cache.get(target.key, None) is not None:
                self._handle_one(target, result)
            elif quota > 0:
                quota -= 1
                outcome = self._handle_one(target, result)
                if outcome == "stripped":
                    result.stripped += 1
            else:
                self._strip_target(target, "历史预算已满，图片未处理")
                result.stripped += 1

        # 深层历史：只注入缓存命中；未命中直接剥离（Phase 2 后台缓存二阶段）
        for target in deep_targets:
            if self.cache.get(target.key, None) is not None:
                self._handle_one(target, result)
            else:
                self._strip_target(target, "深层历史未缓存，图片未处理")
                result.stripped += 1

        self._maybe_inject_followup(ir, len(golden_targets) + len(deep_targets), len(current_targets) > 0)
        return result

    def _maybe_inject_followup(self, ir: IRRequest, history_image_count: int, current_had_image: bool) -> None:
        """spec §5.8 追问检测：当前轮 user 是纯文本（无图）且窗口内历史有图时，
        向当前轮注入「描述未覆盖请重发图+问题，禁止编造」提示。

        history_image_count / current_had_image 须在图片处理前统计并传入（处理后图块已被
        替换成描述，无法再数）；process() 里用 len(golden_targets)+len(deep_targets) 和
        bool(current_targets)。
        """
        if history_image_count == 0:
            return
        current = self._current_turn(ir)
        if current is None or current.role != "user":
            return
        if current_had_image:
            return  # 当前轮原本有图，非纯文本追问
        if not self._current_question(ir):
            return  # 无文字，不算追问
        note = (
            f"[系统：用户之前发送了 {history_image_count} 张图片，描述已在上文注入。"
            "请优先从这些描述回答；若追问的细节在描述中未覆盖，"
            "必须如实告知用户「需要重新查看原始图片，请重新发送图片并附上问题」，"
            "不要猜测或编造图片中未描述的细节。]"
        )
        current.content.insert(0, ContentBlock(type="text", text=note))

    def _handle_one(self, target: _ImageTarget, result: ProcessResult, question: str | None = None) -> str:
        # spec §5.4：当前轮带用户问题时用 Tier2 聚焦（URL+问题缓存键），否则 Tier1。
        key = target.key
        cached = self.cache.get(key, question)
        if cached is not None:
            self._inject(target, cached, result)
            return "injected"
        try:
            tier = 2 if question else 1
            desc = self._describe_with_retry(target.image, question, tier, result)
            self.cache.put(key, question, desc)
        except Exception as exc:  # noqa: BLE001 - fail-open on ANY VLM failure
            self._strip_target(target, self._fail_open_text(exc))
            result.fail_open = getattr(exc, "reason", "VLM_FAILED")
            return "stripped"
        self._inject(target, desc, result)
        return "injected"

    def _describe_with_retry(self, image, question: str | None, tier: int, result: ProcessResult) -> str:
        """VLM 调用 + spec §5.4 重试退避：可重试错误按指数退避重试，
        耗尽或不可重试错误抛给外层 fail-open。"""
        last: VLMError | None = None
        for attempt in range(VLM_MAX_ATTEMPTS):
            try:
                with self.semaphore:
                    desc = self.vlm.describe(image, question=question, tier=tier)
                    result.vlm_calls += 1
                return desc
            except VLMError as exc:
                last = exc
                if attempt == VLM_MAX_ATTEMPTS - 1 or not _is_retryable(exc):
                    raise
                time.sleep(self._backoff(attempt))
            except Exception:
                raise  # 非 VLMError 不重试，交给 fail-open
        raise last  # pragma: no cover - 循环内必然 return 或 raise

    @staticmethod
    def _backoff(attempt: int) -> float:
        """指数退避 VLM_BACKOFF_BASE·2^(attempt) 秒 + [0, VLM_BACKOFF_JITTER) 抖动（spec §5.4）。"""
        return VLM_BACKOFF_BASE * (2**attempt) + random.uniform(0, VLM_BACKOFF_JITTER)

    @staticmethod
    def _fail_open_text(exc: Exception) -> str:
        reason = getattr(exc, "reason", type(exc).__name__)
        return f"看不到图：视觉模型调用失败（{reason}），请更换多模态模型或检查 VLM 配置，不要编造内容。"

    @staticmethod
    def _inject(target: _ImageTarget, desc: str, result: ProcessResult) -> None:
        Pipeline._apply(target, Pipeline._format_desc(desc, target))
        result.injected += 1

    @staticmethod
    def _format_desc(desc: str, target: _ImageTarget) -> str:
        if target.msg_count > 1:
            return f"[[图片{target.index + 1}]] [图片描述] {desc}"
        return f"[图片描述] {desc}"

    @staticmethod
    def _strip_target(target: _ImageTarget, reason: str) -> None:
        Pipeline._apply(target, f"[图片已省略] {reason}")

    @staticmethod
    def _apply(target: _ImageTarget, text: str) -> None:
        if target.data_url is not None:
            _replace_text_at(target.msg, target.path, target.data_url, text)
        else:
            blocks = _blocks_at(target.msg, target.path)
            blocks[target.path[-1]] = ContentBlock(type="text", text=text)
        for path, url in target.cleanup:
            _replace_text_at(target.msg, path, url, "[图片]")

    def _collect_images(self, ir: IRRequest) -> list[tuple[Message, list[_ImageTarget]]]:
        """最近 ANALYZE_DEPTH_LIMIT 条 user/tool 消息，从新到旧逐条抽取图片目标。"""
        msgs = [m for m in ir.messages if m.role in ("user", "tool", "assistant")][-ANALYZE_DEPTH_LIMIT:]
        return [(msg, self._collect_message_targets(msg)) for msg in reversed(msgs)]

    @staticmethod
    def _current_turn(ir: IRRequest) -> Message | None:
        for msg in reversed(ir.messages):
            if msg.role in ("user", "tool"):
                return msg
        return None

    @staticmethod
    def _current_question(ir: IRRequest, limit: int = 200) -> str | None:
        """当前轮用户消息的纯文本（截断）作为 Tier2 聚焦问题；无文本返回 None。"""
        msg = Pipeline._current_turn(ir)
        if msg is None or msg.role != "user":
            return None
        text = "".join(b.text or "" for b in msg.content if b.type == "text").strip()
        if not text:
            return None
        return text[:limit] if len(text) > limit else text

    @staticmethod
    def _collect_message_targets(msg: Message) -> list[_ImageTarget]:
        """抽取单条消息的图片目标：结构化块（含嵌套 tool_result）与文本内嵌 data URL。"""
        block_targets: list[_ImageTarget] = []
        target_by_url: dict[str, _ImageTarget] = {}
        embedded: list[tuple[tuple[int, ...], str]] = []

        def walk(blocks: list[ContentBlock], prefix: tuple[int, ...]) -> None:
            for i, block in enumerate(blocks):
                path = prefix + (i,)
                if block.type == "image" and block.image:
                    target = _ImageTarget(msg=msg, image=block.image, path=path)
                    block_targets.append(target)
                    if block.image.url:
                        target_by_url.setdefault(block.image.url, target)
                elif block.type == "text" and block.text:
                    for url in extract_data_urls(block.text):
                        embedded.append((path, url))
                elif block.type == "tool_result" and block.tool_result_content:
                    walk(block.tool_result_content, path)

        walk(msg.content, ())

        # IR 解析器会把文本内嵌 data URL 同时拆成 image 块；同一 URL 两种形态只算一张图，
        # 描述注入到图片块，文本里的 URL 仅替换为短标记，避免重复注入。
        for path, url in embedded:
            existing = target_by_url.get(url)
            if existing is not None:
                existing.cleanup.append((path, url))
            else:
                block_targets.append(_ImageTarget(msg=msg, image=ImageBlock(url=url), path=path, data_url=url))

        block_targets.sort(key=lambda t: t.path)
        for idx, target in enumerate(block_targets):
            target.index = idx
            target.msg_count = len(block_targets)
        return block_targets

    @staticmethod
    def _strip_all(ir: IRRequest, reason: str) -> int:
        n = 0
        for msg in ir.messages:
            for target in Pipeline._collect_message_targets(msg):
                Pipeline._strip_target(target, reason)
                n += 1
        return n

    @staticmethod
    def _budget(ir: IRRequest, cfg: ProxyConfig) -> float:
        context = 128_000  # 默认窗口；可配 relay 时按模型取
        text_tokens = sum(
            len(Pipeline._text_without_data_urls(text).encode("utf-8")) // 2
            for msg in ir.messages
            for text in Pipeline._iter_text(msg.content)
        )
        available = context * CONTEXT_SAFETY_MARGIN - text_tokens
        return available / AVG_DESC_BUDGET

    @staticmethod
    def _text_without_data_urls(text: str) -> str:
        """剔除字符串内嵌 data URL（base64 会被替换成 [图片描述]，不应计入文本预算；
        否则 T3/T4 的工具返回图会把预算算爆成 CONTEXT_FULL）。"""
        return _DATA_URL_RE.sub("", text)

    @staticmethod
    def _iter_text(blocks: list[ContentBlock]):
        for block in blocks:
            if block.type == "text" and block.text:
                yield block.text
            elif block.type == "tool_result" and block.tool_result_content:
                yield from Pipeline._iter_text(block.tool_result_content)
