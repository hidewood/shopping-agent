"""本地 embedding 向量化与相似度检索（RAG 模糊主题匹配）。

用 sentence-transformers 把商品标签（英文 + 中文别名）向量化，当精确/别名
匹配失败时，用余弦相似度找最接近的标签，解决"小清新""高级感"这类模糊主题。

依赖未安装或模型加载失败时降级为空索引（search 返回空），不影响现有检索。
"""

from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_MODEL = "BAAI/bge-large-zh-v1.5"


class EmbeddingIndex:
    """商品标签的向量索引，用于模糊主题的相似度匹配。"""

    def __init__(self, entries: list[tuple[str, str]], model_name: str = DEFAULT_MODEL):
        """entries: [(canonical_tag, search_text), ...]，search_text 含中英文别名。"""
        self.entries = entries  # [(tag, search_text)]
        self._model: Any = None
        self._vectors: np.ndarray | None = None
        self._load_model(model_name)
        self._encode([text for _, text in entries])

    def _load_model(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)  # 自动用 GPU（若可用）
        except Exception:
            self._model = None  # 降级：embedding 功能关闭

    def _encode(self, texts: list[str]) -> None:
        if self._model is None or not texts:
            self._vectors = None
            return
        self._vectors = self._model.encode(texts, normalize_embeddings=True)

    @property
    def available(self) -> bool:
        return self._model is not None and self._vectors is not None

    def search(self, query: str, top_k: int = 2, threshold: float = 0.53) -> list[tuple[str, float]]:
        """返回相似度超过 threshold 的 (tag, similarity) 列表，按相似度降序。

        threshold 0.53 是「具象词正确匹配（海边→Beach 0.57）与抽象词噪声
        （高级感→Highway 0.52）」之间的分界：宁可漏匹配也不误匹配。
        """
        if not self.available or not query.strip():
            return []
        q = self._model.encode([query], normalize_embeddings=True)[0]
        sims = np.dot(self._vectors, q)  # 已归一化，点积即余弦相似度
        results = []
        for idx in np.argsort(sims)[::-1][:top_k]:
            tag, _ = self.entries[idx]
            similarity = float(sims[idx])
            if similarity >= threshold:
                results.append((tag, similarity))
        return results


# 懒加载单例：避免每次创建 Agent 都重新加载模型
_index: EmbeddingIndex | None = None


def get_index(entries: list[tuple[str, str]]) -> EmbeddingIndex:
    """返回全局单例索引（首次调用时构建）。"""
    global _index
    if _index is None:
        _index = EmbeddingIndex(entries)
    return _index
