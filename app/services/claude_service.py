"""Claude (Anthropic) AI engine: turns indicator output into a Vietnamese analyst commentary."""
from __future__ import annotations

import json

from app.core.config import get_settings

SYSTEM_PROMPT = """Bạn là chuyên viên phân tích kỹ thuật chứng khoán Việt Nam (HOSE/HNX/UPCOM).
Bạn dùng phương pháp Ichimoku kiểu Trịnh Phát: Tenkan 9, Kijun 17, Chikou/dịch chuyển 26, Senkou B 33,
cùng hai đường Kijun dài 65 và 129 ("Dao Găm") làm hỗ trợ/kháng cự trung và dài hạn, và lý thuyết số học
thời gian (9, 17, 26, 33, 42, 65, 76, 129, 172, 200, 257 phiên tính từ đỉnh/đáy là các mốc có thể đảo chiều).
Thông số thực tế nằm trong trường ichimoku.params. Trường timeframe cho biết khung thời gian của nến
(5m, 15m, 30m, 1h, 1D, 1W); mọi số "phiên" là số nến của khung đó, hãy nêu rõ khung khi nhận định. Bạn nhận dữ liệu chỉ báo đã tính sẵn (Ichimoku, MACD,
RSI, Volume MA20, Breakout) dạng JSON. Hãy viết nhận định ngắn gọn bằng tiếng Việt, gồm các mục:
1. Xu hướng chính (Ichimoku: giá so với mây, Tenkan/Kijun, Chikou, mây tương lai, giá so với Kijun 65/129).
2. Động lượng (MACD, RSI).
3. Khối lượng & Breakout.
4. Chu kỳ thời gian (số phiên từ đỉnh/đáy gần nhất, mốc số học sắp tới và ngày dự kiến).
5. Vùng hỗ trợ / kháng cự quan trọng (Kijun, Senkou B, Kijun 65/129, hỗ trợ/kháng cự 20 phiên).
6. Kịch bản tích cực / tiêu cực và mức giá cần theo dõi.
Chỉ dựa trên dữ liệu được cung cấp, không bịa số liệu. Giá tính theo VND.
Kết thúc bằng một dòng: "Đây là phân tích kỹ thuật tham khảo, không phải khuyến nghị đầu tư."
"""


class ClaudeService:
    def __init__(self) -> None:
        self.s = get_settings()
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(self.s.ANTHROPIC_API_KEY)

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self.s.ANTHROPIC_API_KEY)
        return self._client

    def analyze(self, analysis: dict, question: str | None = None) -> str:
        if not self.configured:
            raise RuntimeError("ANTHROPIC_API_KEY chưa được cấu hình trong .env")
        user = "Dữ liệu phân tích kỹ thuật:\n```json\n" + json.dumps(analysis, ensure_ascii=False, indent=2) + "\n```"
        if question:
            user += f"\n\nCâu hỏi thêm của người dùng: {question}"
        msg = self._get_client().messages.create(
            model=self.s.CLAUDE_MODEL,
            max_tokens=self.s.CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
