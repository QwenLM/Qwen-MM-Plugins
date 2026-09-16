"""Omni-model tools for open-ended perception, ASR, captioning, grounding, and event counting.

The A/V tools read video frames and the embedded audio track together via the Qwen-Omni model
(``shared.api_omni``). Every public module exports ``TOOL`` + ``handle``; ``_common`` is shared
plumbing (no TOOL) and is skipped by the registry.
"""
