# PStock

Ứng dụng phân tích kỹ thuật cổ phiếu Việt Nam: **FastAPI** + dữ liệu **Vietcap / SSI FastConnect** + nhận định AI bằng **Claude** + dashboard web.

## 1. Cài đặt & chạy (Windows)

1. Cài **Python 3.10+** (tích chọn *Add python.exe to PATH*).
2. Giải nén project, mở file `.env`:
   - `SSI_CONSUMER_ID`, `SSI_CONSUMER_SECRET` đã điền sẵn từ file key SSI.
   - Điền `ANTHROPIC_API_KEY` (lấy tại https://console.anthropic.com) để dùng nhận định AI.
3. Double-click **`run.bat`**. Lần đầu script tạo `.venv`, cài thư viện rồi mở http://127.0.0.1:8000.

Để mở website từ máy khác cùng mạng LAN, đặt `APP_HOST=0.0.0.0` trong `.env`, chạy lại `run.bat`. `0.0.0.0` nghĩa là
"nghe trên mọi card mạng", không phải địa chỉ để mở: cửa sổ server in ra các địa chỉ dùng được, ví dụ
http://127.0.0.1:8000 (trên chính máy này) và http://192.168.3.38:8000 (từ máy khác). Sau đó cho phép
cổng TCP 8000 qua Windows Firewall (PowerShell chạy **as Administrator**, chỉ áp dụng mạng Private):
```powershell
New-NetFirewallRule -DisplayName "PStock LAN (TCP 8000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private -RemoteAddress LocalSubnet
```
Nếu mạng hiện tại có profile **Public** (`Get-NetConnectionProfile`), chỉ khi đó là mạng LAN đáng tin cậy mới dùng
lệnh giới hạn thiết bị trong cùng subnet:
```powershell
New-NetFirewallRule -DisplayName "PStock LAN (TCP 8000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Public -RemoteAddress LocalSubnet
```
Máy khác truy cập `http://<địa-chỉ-IPv4-của-máy-chạy-PStock>:8000` (xem bằng `ipconfig`). Chỉ nên bật trên mạng
đáng tin cậy; đặt `APP_API_KEY` trong `.env` nếu cần bảo vệ các API.

Kiểm tra kết nối:
```bat
.venv\Scripts\python scripts\check_connection.py FPT
```

## 1b. Cài đặt & chạy trên Linux (Linux Mint / Ubuntu / Debian)

Cần Python 3.10+ (Linux Mint 21 có 3.10, Mint 22 có 3.12) và gói venv:
```bash
sudo apt install python3 python3-venv python3-pip      # một lần
cd ~/accstock                                           # thư mục project
chmod +x accstock && ./accstock install                 # tạo .venv, cài requirements.txt, tạo lệnh `accstock`
nano .env  telegram.env                                 # điền key SSI / Anthropic / Telegram
```
`./accstock install` tạo `.venv`, cài thư viện theo `requirements.txt`, tạo `.env` / `telegram.env` từ file mẫu nếu chưa có
(quyền 600) và tạo lệnh `~/.local/bin/accstock`. Có thể bỏ qua bước này và chạy `accstock start`: lệnh sẽ tự cài `.venv`
và requirements nếu môi trường chưa có. Khi `.venv` đã tồn tại, `accstock start` chỉ chạy app, không cài/cập nhật thư viện.
Chạy lại `./accstock install` bất cứ lúc nào để cập nhật thư viện. Sau khi cài, gõ `accstock ...` ở bất kỳ thư mục nào
(lệnh `~/.local/bin/accstock` trỏ về file `accstock` trong project). Máy đã cài bằng `./pstock install` trước đây: chạy
`./accstock install` một lần để lệnh `accstock` trỏ sang file mới.

| Lệnh | Việc |
|---|---|
| `./accstock install` | Cài / cập nhật `.venv`, thư viện và lệnh `accstock` |
| `accstock start` | Nếu thiếu `.venv` thì tự cài môi trường và requirements; nếu đã có thì chỉ chạy app ngầm, đợi tới khi `/health` trả lời rồi in địa chỉ http://127.0.0.1:8000 |
| `accstock start 8888` | Như trên nhưng chạy ở cổng 8888 (cổng mặc định 8000). `stop` / `status` / `logs` tự theo cổng đang chạy |
| `accstock stop` | Dừng app (SIGTERM, sau 10 giây chưa dừng thì SIGKILL) |
| `accstock restart` | Dừng rồi chạy lại (sau khi cập nhật code), giữ cổng đang dùng; `accstock restart 8888` = chạy lại ở cổng 8888 |
| `accstock status` | Đang chạy hay không, PID, thời gian chạy, địa chỉ, `/health`, bộ nhớ (mã thoát 3 = đã dừng) |
| `accstock logs` | Xem log server (`data/logs/server.log`), Ctrl+C để thoát |
| `accstock auto [on\|off] [board\|daily]` | Xem / bật / tắt gửi Telegram tự động (không cần chạy app), xem mục 4c |

- Mở từ máy khác trong mạng: đặt `APP_HOST=0.0.0.0` trong `.env` (giống Windows) rồi `accstock restart`, sau đó mở
  `http://<IP máy Linux>:8000` (`accstock status` in sẵn các địa chỉ). Nếu bật tường lửa ufw: `sudo ufw allow from 192.168.8.0/24 to any port 8000 proto tcp`.
  Nên đặt `APP_API_KEY` trong `.env`. `PSTOCK_HOST` trong `pstock.conf` (nếu có) được ưu tiên hơn `APP_HOST`. Đổi cổng mặc định lâu dài: ghi `PSTOCK_PORT=8010` vào `pstock.conf`; `accstock start <cổng>` luôn được ưu tiên.
- Lệnh `accstock` luôn chạy app theo giờ Việt Nam (`TZ=Asia/Ho_Chi_Minh`), dù máy Linux đặt múi giờ nào.
- `accstock stop` chỉ dừng đúng tiến trình do `accstock start` tạo (kiểm tra PID trong `data/pstock.pid`).
- Lịch gửi Telegram (mục 4b, 4c) dùng Task Scheduler của Windows nên **chưa chạy trên Linux** (chưa có cron);
  `accstock auto` chỉ đổi công tắc bật/tắt.

## 2. Chức năng

| Thành phần | Mô tả |
|---|---|
| **Dữ liệu** | **Vietcap** (ưu tiên, API biểu đồ công khai), tự chuyển sang **SSI** (SDK chính thức `ssi-sdk`, API v3) nếu Vietcap lỗi. Đổi bằng `DATA_SOURCE` trong `.env`: `auto` (Vietcap → SSI, mặc định), `vietcap`, `ssi`, `ssi_first` (SSI → Vietcap). Khung **5m, 15m, 30m, 60m, Ngày, Tuần**. Cache CSV `data/cache/<MÃ>_<KHUNG>.csv`, tự tải bổ sung phần mới. |
| **Import EOD (lịch sử Ngày)** | Nút **⬆️ Import EOD** ở tab *Phân tích*: nạp file CSV CafeF `<Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume>` (giá nghìn đồng, tự nhân 1.000) hoặc file `.zip` chứa các file đó (vd. `CafeF.SolieuGD.Upto…zip`), chọn được nhiều file. Chỉ giữ mã 3 ký tự: bỏ chứng quyền / ETF (8 ký tự) và chỉ số. Dữ liệu được ghép vào cache Ngày `data/cache/<MÃ>_1D.csv` (cột `source` = `cafef`; trùng ngày thì dữ liệu import thắng), khung Tuần được dựng lại từ lịch sử Ngày này. Sau đó app chỉ tải thêm các phiên mới nhất từ Vietcap / SSI. **Dùng giá đã điều chỉnh.** Nếu sau này có sự kiện quyền (giá Vietcap lệch > 0,5% so với dữ liệu đã lưu ở các phiên trùng), nguồn hiển thị thêm “⚠ giá điều chỉnh đã thay đổi, cần Import lại” (danh sách trong `data/cache/eod_readjust.json`) cho tới khi import lại mã đó. 5m–60m vẫn lấy từ Vietcap / SSI. |
| **Realtime** | SSI `StreamingService` (WebSocket): mã đang mở trên biểu đồ được đăng ký nến 1 phút, gộp thành nến của khung đang xem; dashboard cập nhật nến cuối mỗi 5 giây. |
| **Ichimoku Trịnh Phát** | Tenkan 9, Kijun 17, Chikou/dịch 26, Senkou A = (Tenkan+Kijun)/2, Senkou B 33, mây tương lai 26 phiên. Thêm **Kijun 65** (hỗ trợ/kháng cự trung hạn) và **Kijun 129 – "Dao Găm"** (dài hạn). Tất cả = trung điểm (cao nhất + thấp nhất)/2 của số phiên tương ứng. Đổi `ICHI_PRESET=classic` để dùng 9-26-26-52 + 129. |
| **Số học thời gian** | Dãy 9, 17, 26, 33, 42, 65, 76, 129, 172, 200, 257 (17 = 9+9−1, 26 = 9+17, 65 = 33+33−1, 129 = 65+65−1…). Đếm số phiên từ đỉnh/đáy lớn nhất trong 129 phiên, báo khi chạm mốc (±1) và ngày dự kiến của mốc kế tiếp. |
| **MACD** | 12/26/9, tín hiệu cắt, histogram. |
| **RSI** | 14 (Wilder), vùng 30/50/70. |
| **Volume MA20 & Breakout** | Breakout = đóng cửa vượt đỉnh 20 phiên trước + KL ≥ 1,5× MA20. Breakdown tương tự với đáy. |
| **Chấm điểm** | Điểm -100…+100 (có cộng/trừ theo vị trí giá so với Kijun 65/129), xếp hạng Strong Bullish → Strong Bearish (logic trong `stock_scanner.py`). |
| **Claude AI** | Gửi kết quả chỉ báo cho Claude, nhận nhận định tiếng Việt. Không gửi dữ liệu thô hay key. |
| **Dashboard** | Biểu đồ nến + Ichimoku + KL, MACD, RSI, **Điểm kỹ thuật** theo từng nến (cột màu theo xếp hạng, vạch ±25 / ±60); bảng tổng hợp; hỏi Claude; tab Bảng điện; tab Scanner; tab Xếp hạng ACC. |
| **Bảng điện** | **Trang chủ** (`http://127.0.0.1:8000/`; tab *Cổ phiếu* = `/#stock`, *Scanner* = `/#scan`, *Xếp hạng ACC* = `/#acc`; các khung thời gian chỉ có ở tab *Cổ phiếu*):<br>• **Header chung ở mọi tab**: thẻ chỉ số VNINDEX, VN30, HNX, HNX30, UPCOM (điểm, thay đổi, giá trị (tỷ), số mã ▲ tăng (trần), ■ đứng, ▼ giảm (sàn)) và chú thích màu, tự cập nhật. Mỗi thẻ có **đồ thị trong phiên** theo **1 phút** hoặc **3 phút** (nút chọn ở dòng chú thích): đường xanh lá khi trên tham chiếu, đỏ khi dưới, nét đứt vàng = tham chiếu, vùng xanh nhạt = khối lượng, mũi tên ▲/▼ = hướng của chỉ số; rê chuột để xem giờ và điểm. Đường nét đứt vàng = điểm tham chiếu của phiên, luôn hiện trên đồ thị. Bảng điện, thẻ chỉ số và đồ thị giữ nguyên số liệu phiên gần nhất cho tới **8:56 sáng ngày làm việc kế tiếp**, rồi chuyển sang phiên mới: đồ thị vẽ lại từ 9:00 (trục thời gian 9:00–14:45).<br>• Hai bảng **cố định ở cột 1 và cột 2**: **Quan Tâm** (mã của anh/chị: gõ mã vào ô trong bảng rồi bấm + / Enter, nhiều mã cách nhau bằng dấu phẩy; rê chuột vào mã rồi bấm ✕ để bỏ; lưu trên trình duyệt) và **VN30** (rổ VN30 thực tế, hoặc ngành "VN30" trong file Excel). Ở hai bảng này, bấm tiêu đề cột **Mã**, **+/-** hoặc **KL** để sắp xếp (mỗi bảng riêng), bấm lần nữa để đảo chiều, lần thứ ba để về thứ tự mặc định (Quan Tâm: thứ tự đã thêm; VN30: % thay đổi từ cao tới thấp); lựa chọn được lưu trên trình duyệt. Các ngành khác xếp ở phần còn lại; *Xếp ngành theo % thay đổi* chỉ sắp xếp các ngành này.<br>• Các nhóm ngành (33 ngành mặc định trong `app/services/sectors.py`, đổi được bằng nút Excel), mỗi mã một dòng **Mã · Điểm · Giá · +/- · KL**. Nền màu: trần tím, tăng xanh (4 mức), tham chiếu vàng, giảm đỏ (4 mức), sàn xanh lam. % của ngành tính theo giá trị giao dịch.<br>• **Điểm** = điểm kỹ thuật khung Ngày, tính ngầm lần đầu mở trong ngày (khoảng 1–2 phút cho ~290 mã).<br>• Rê chuột để xem TC/trần/sàn, cao/thấp, giá trị, khối ngoại. Bấm vào mã để mở biểu đồ.<br>• Nút *Quan tâm* chỉ hiện bảng Quan Tâm. Cập nhật mỗi 5 giây trong giờ giao dịch, 60 giây ngoài giờ.<br>• **📊 Excel** (nhập/xuất danh sách mã của các ngành): *Xuất Excel* tải file `Ngành | Mã` (mỗi dòng một mã); sửa trong Excel rồi *Nhập Excel / CSV…*: **xoá toàn bộ các bảng ngành và mã hiện có** rồi mới nạp file, nên bảng điện chỉ còn đúng các ngành trong file (lưu ở `data/sectors.json`) cùng hai bảng cố định. Ngành "Quan Tâm" trong file thay danh sách Quan Tâm; ngành "VN30" thay rổ VN30 thực tế. File được kiểm tra trước khi xoá: file lỗi thì danh sách cũ giữ nguyên. File xuất có cả các dòng Quan Tâm và VN30. *Khôi phục danh sách mặc định* quay về danh sách có sẵn. Tên ngành không phân biệt hoa/thường, có dấu/không dấu; mã sai hoặc trùng bị bỏ qua và được báo lại. Cổ phiếu mạnh/yếu gửi Telegram cũng quét theo danh sách này.<br>• **📨 Gửi Telegram** (cùng hàng với *Tất cả* / *Quan tâm* / *Excel*, sát lề phải): nhập ngưỡng (mặc định 3%, lưu trên trình duyệt), bấm *Xem trước* hoặc *Gửi tin nhắn*; khung xem trước xổ xuống, bấm *▲ Thu gọn* để các bảng trở lại vị trí ban đầu. Tin gồm **1. Co Phieu Manh** (% so với tham chiếu ≥ +ngưỡng, xếp từ cao tới thấp) và **2. Co Phieu Yeu** (≤ −ngưỡng, xếp từ thấp tới cao), dạng bảng cố định: Ma · Gia (nghìn) · +/-% · KL (K) · %V20p (KL hôm nay so với KL trung bình 20 phiên trước; trong phiên là KL tính tới thời điểm gửi). Mỗi dòng có ký hiệu màu ở cuối (Telegram không cho bot đổi màu chữ): 🩷 tăng > 6.5% · 🟢 tăng · 🟡 tham chiếu · 🔴 giảm · 🔵 giảm > 6.5% · ⚪ chưa khớp. Mục **3. Quan Tam (A-Z)**: toàn bộ mã trong bảng Quan Tâm, xếp theo tên mã. Gửi tới `TELEGRAM_CHAT_ID` trong `telegram.env`; bản tin này còn được gửi tự động theo lịch (mục 4c).<br>• **Tự động:** hai ô tick cùng hàng: ☑ *Bản tin bảng điện* (lịch 12 khung giờ, mục 4c) và ☑ *BO/BD 15:05* (mục 4b). Bỏ tick = tắt gửi tự động (chữ bị gạch ngang), tick lại = bật; lưu trên server (`data/board_settings.json`), áp dụng ngay cho lần chạy kế tiếp, không cần sửa Task Scheduler. Nút *Gửi tin nhắn* vẫn gửi tay bình thường. |
| **Scanner · Sức mạnh cổ phiếu** | Tab *Scanner*, bảng trên: mọi mã trên Bảng điện (VN30, các ngành, Quan Tâm), khung Ngày, tính ngầm 1 lần/ngày (cùng lượt tính Điểm của bảng điện; nút *↻ Tính lại* để tính lại với dữ liệu mới nhất).<br>• **RS/VNI** = (1 + %CP 3 tháng) / (1 + %VN-Index 3 tháng); **Đường RS** = giá CP / VN-Index: ▲ ở đỉnh 52 tuần, ★ lập đỉnh trước giá.<br>• **RS Rating** (IBD/O'Neil, 1–99) = percentile của 0.4×ROC63 + 0.2×ROC126 + 0.2×ROC189 + 0.2×ROC252 trong toàn bộ các mã; ≥ 80 = dẫn dắt.<br>• **Điểm KT** 0–100, chia nhóm Xu hướng (trên mây 15, Tenkan > Kijun 10, trên Kijun 65 & 129 15) · Động lượng (MACD > Signal và histogram tăng 10, RSI 50–70 10, RS Rating ≥ 80 10) · Dòng tiền (KL > 1.5× MA20 trong phiên tăng 15, vượt đỉnh 20 tuần / 52 tuần 15).<br>• **Trend Template** (Minervini): Giá > MA50 > MA150 > MA200 · MA200 đi lên ≥ 1 tháng · cao hơn đáy 52 tuần ≥ 30% · cách đỉnh 52 tuần ≤ 25% · RS Rating ≥ 70.<br>• **Ngành**: RS Rating trung bình của ngành (bảng bên trái, bấm để lọc). **Tổng hợp** = 40% RS Rating + 40% Điểm KT + 20% ngành. Lọc theo ngành, Trend Template, 3 ngành mạnh nhất, RS tối thiểu; bấm tiêu đề cột để sắp xếp. Chỉ hỗ trợ sàng lọc, không thay thế quyết định đầu tư. |
| **Xếp hạng ACC · Stock Strength Monitor V1** | Tab *Xếp hạng ACC* (`/#acc`), theo `Requirements/Stock_Strength_Monitor_V1.md`: mọi mã trên Bảng điện, benchmark **VN30**, khung Ngày, chỉ dùng **phiên đã đóng cửa** (trong giờ giao dịch bỏ nến đang chạy; sau 15:00 bấm *↻ Tính lại*). Tính ngầm cùng lượt với Điểm / Scanner.<br>• **5 thành phần 0–100**: RS 30% (Excess Return so với VN30 1M/3M/6M/12M → percentile trong universe), Trend 25% (MA20/50/100/200), Momentum 20% (RSI, MACD, ADX, ROC20 percentile), Flow 15% (RVOL, OBV, CMF, MFI), Ichimoku 10% (9-17-26, dịch 26).<br>• **Swing** = 0.30·RS Swing + 0.70·Other, **Position** = 0.30·RS Position + 0.70·Other, **Combined** = trung bình hai điểm. **Setup** (chất lượng điểm mua) và **Risk** (quá nóng) là hai điểm riêng, không cộng/trừ vào Combined.<br>• **Tín hiệu**: OVERHEATED → BREAKOUT → STRONG BUY WATCH → STRONG → ACCUMULATION → WEAK → NEUTRAL. **Δ** = Combined so với phiên trước, ▲▼ = thay đổi thứ hạng.<br>• Thẻ tổng quan thị trường (Strong/Weak, % trên MA20/50/200, phân bổ điểm, ngành mạnh, số mã theo tín hiệu); 4 chế độ xếp hạng Combined/Swing/Position/Setup; lọc Strength, RS, Setup, Risk, Trend, tín hiệu, ngành, Top 10/20/50; bấm tiêu đề cột để sắp xếp; bấm dòng để xem chi tiết từng điều kiện, bấm đúp để mở biểu đồ; **⬇️ CSV** xuất các dòng đang hiển thị (Python / Excel). Nhãn theo thuật toán, không phải khuyến nghị mua bán; các ngưỡng cần backtest. |
| **Phân tích · Wyckoff × Ichimoku** | Tab *Phân tích* (`/#analysis`), khung Ngày hoặc Tuần: Wyckoff cho biết *giá đi được bao xa*, Ichimoku cho biết *trên đường đi có chướng ngại gì*.<br>• **Vùng TR** tự dò (đoạn đi ngang kết thúc ngay trước nến phá vỡ gần nhất, hoặc TR đang diễn ra); có thể chọn tay ngày bắt đầu / kết thúc, box, số box đảo chiều, hướng (lên / xuống).<br>• **Đếm ngang P&F**: Count = số cột P&F trong TR × box × 3. **T1** (thận trọng) = đáy TR + Count, **T2** = giữa TR + Count, **T3** (toàn bộ nguyên nhân) = đỉnh TR + Count (hướng xuống đối xứng).<br>• **Bản đồ Ichimoku Ngày + Tuần**: Kijun (phẳng hay không), Kijun 65/129, biên mây hiện tại và +26, điểm xoắn mây. Mục tiêu nằm trong ±x% (1/2/3/5) của một mốc là **vùng hợp lưu**, chấm điểm (mốc Tuần ×2, Kijun phẳng +1…).<br>• **Kịch bản**: A bị chặn trước mục tiêu bởi mây dày / xoắn mây / Kijun Tuần (chốt một phần) · B mục tiêu trùng Kijun Tuần phẳng (hợp lưu vàng, nắm giữ, theo dõi KL) · C hợp lưu thường · D đường thoáng.<br>• Còn có: thang giá gộp mọi mốc, sự kiện Spring / UT / SOS / LPS (kèm KL), R:R tới từng mục tiêu, biểu đồ P&F để kiểm tra số cột, cảnh báo (Count quá lớn, TR ngắn, KL phá vỡ yếu). Count tỉ lệ với số nhịp dao động nên TR dài cho mục tiêu rất xa: ưu tiên T1. Công cụ tham chiếu, cần backtest. |

## 3. API

Swagger UI: http://127.0.0.1:8000/docs

`timeframe` = `5m` · `15m` · `30m` · `1h` (hoặc `60m`) · `1D` (mặc định) · `1W`

| Method | Endpoint | Mô tả |
|---|---|---|
| GET | `/api/market/ohlcv/{symbol}?timeframe=15m&days=&refresh=false` | Dữ liệu OHLCV |
| GET | `/api/market/live/{symbol}?timeframe=5m` | 2 nến cuối (REST + stream), tự đăng ký stream cho mã |
| GET | `/api/market/stream/status` | Trạng thái WebSocket SSI |
| GET | `/api/market/timeframes` | Danh sách khung thời gian |
| GET | `/api/market/watchlist` | Danh mục mặc định |
| GET | `/api/board?watch=FPT,HPG` | Bảng điện: chỉ số, nhóm ngành, giá trong phiên, điểm từng mã |
| GET / PUT | `/api/board/settings` | Danh sách Quan Tâm, ngưỡng %, công tắc gửi tự động `auto_board` / `auto_daily` (dùng chung với lịch gửi Telegram), vd. `{"auto_board": false}` |
| GET | `/api/board/index-chart?interval=1` | Đồ thị trong phiên của 5 chỉ số (1 hoặc 3 phút), tham chiếu, khối lượng |
| GET | `/api/board/indexes` | Chỉ các thẻ chỉ số (header chung ở mọi tab) |
| GET | `/api/board/strength?watch=FPT` | Scanner: RS Rating, Điểm KT, Trend Template, ngành, tổng hợp (poll tới khi `progress.done == total`) |
| GET | `/api/analysis/{symbol}/wyckoff?timeframe=1D&start=&end=&box=&reversal=3&tol=3&direction=auto` | Wyckoff (đếm ngang P&F) × Ichimoku: TR, Count, T1–T3, vùng hợp lưu, kịch bản, R:R, dữ liệu biểu đồ. `timeframe` chỉ `1D` / `1W`; `start`/`end` = `YYYY-MM-DD` để chọn tay TR |
| POST | `/api/analysis/eod/import?filename=` | Import lịch sử End Of Day (CSV / zip CafeF, body = nội dung file) vào cache Ngày. Trả về số mã, số phiên, số phiên mới, số dòng chứng quyền / chỉ số bị bỏ |
| GET | `/api/board/acc?watch=FPT` | Xếp hạng ACC: RS vs VN30, Trend, Momentum, Flow, Ichimoku, Swing/Position/Combined, Setup, Risk, tín hiệu, Δ phiên trước, tổng quan thị trường |
| POST | `/api/board/strength/refresh` | Tính lại RS Rating / điểm ACC / điểm hôm nay |
| GET | `/api/board/sectors` · `/api/board/sectors.xlsx` | Danh sách ngành (JSON / tải Excel) |
| POST | `/api/board/sectors/import?filename=x.xlsx` | Nhập danh sách ngành (nội dung file .xlsx/.csv là body) |
| DELETE | `/api/board/sectors` | Khôi phục danh sách ngành mặc định |
| POST | `/api/board/telegram` | `{"threshold": 3, "watch": "FPT,HPG", "dry_run": false}` → gửi cổ phiếu mạnh/yếu qua Telegram (`dry_run` chỉ trả nội dung) |
| GET | `/api/analysis/{symbol}?timeframe=1h` | Toàn bộ chỉ báo + điểm |
| GET | `/api/analysis/{symbol}/chart?timeframe=30m&bars=300` | Dữ liệu vẽ biểu đồ |
| POST | `/api/analysis/scan` | `{"symbols": ["FPT","HPG"], "min_score": 25, "timeframe": "1D"}` |
| GET | `/api/ai/status` | Trạng thái cấu hình Claude |
| POST | `/api/ai/analyze/{symbol}` | `{"question": "...", "timeframe": "1D"}` → nhận định của Claude |

Nếu đặt `APP_API_KEY` trong `.env`, mọi `/api/*` yêu cầu header `X-API-Key`.

## 4. Scripts

```bat
.venv\Scripts\python scripts\fetch_data.py --watchlist     :: tải dữ liệu cả danh mục
.venv\Scripts\python scripts\scan.py --min-score 25         :: quét, lưu data\scan_YYYYMMDD.csv
.venv\Scripts\python scripts\board_alerts.py --dry-run      :: bản tin bảng điện (mục 4c)
.venv\Scripts\python scripts\daily_signals.py --dry-run     :: cảnh báo BO/BD (mục 4b)
.venv\Scripts\python scripts\auto_send.py off               :: tắt gửi Telegram tự động (= auto_send.bat off)
.venv\Scripts\python -m pytest -q                           :: chạy test
```

## 4b. Cảnh báo BO/BD qua Telegram (15:05 các ngày giao dịch)

Task Scheduler (task **"PStock Daily Signals"**) chạy `daily_signals.bat` lúc 15:05 từ thứ 2 đến thứ 6.
Script quét **toàn bộ mã của Bảng điện** (VN30 + mọi bảng ngành + Quan Tâm, mã trùng chỉ lấy một lần; khoảng 270 mã, ~20 giây) theo khung Ngày rồi gửi kết quả qua Telegram. Trong mỗi mục BO / BD, mã xếp theo **Điểm** từ cao tới thấp.
Nút **Gửi BO/BD** ở tab Bảng điện làm đúng việc này và gửi ngay (trước 15:05 thì lấy phiên giao dịch trước).

| Tín hiệu | Điều kiện |
|---|---|
| 🟢 **BO Ichimoku** | Giá đóng cửa vượt lên **trên mây**, trong khi phiên trước còn ở trong hoặc dưới mây |
| 🔴 **BD Ichimoku** | Giá đóng cửa thủng xuống **dưới mây**, trong khi phiên trước còn ở trong hoặc trên mây |
| Xác nhận (✅/❌) | Tenkan/Kijun, Chikou so với giá 26 phiên trước, mây tương lai, giá so với Kijun 129, KL/MA20 |
| BO/BD giá–khối lượng | Vượt đỉnh / thủng đáy 20 phiên với KL ≥ 1.5× MA20 (ký hiệu BO/BD trên biểu đồ) |

Cấu hình nằm trong **`telegram.env`** (đã gitignore): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (nhiều chat thì phân cách bằng dấu phẩy), `TELEGRAM_SEND_EMPTY`.

```bat
:: 1) nhắn /start cho bot trên Telegram, rồi lấy và lưu Chat ID:
.venv\Scripts\python scripts\telegram_chat_id.py --save
.venv\Scripts\python scripts\telegram_chat_id.py --test       :: gửi tin thử
.venv\Scripts\python scripts\daily_signals.py --dry-run       :: xem trước tin nhắn, không gửi
.venv\Scripts\python scripts\daily_signals.py --force         :: gửi ngay, kể cả ngoài ngày giao dịch
```
- Ngày nghỉ/lễ được tự nhận biết: nếu không mã nào có nến của hôm nay thì không gửi. Mã bị tạm ngừng giao dịch không bị báo lại tín hiệu cũ.
- Máy tính phải bật và đã đăng nhập. Nếu máy ngủ lúc 15:05, task chạy ngay khi máy bật lại.
- Bật/tắt nhanh: ô tick **Tự động: BO/BD 15:05** ở tab Bảng điện (khi tắt, task vẫn chạy nhưng không gửi; `--force` vẫn gửi).
- Log: `data\logs\daily_signals.log`. Đổi giờ hoặc tắt task: mở *Task Scheduler* → *PStock Daily Signals*.

## 4c. Bản tin Bảng điện qua Telegram

Thiết lập ngay trên thanh **Bảng điện → Gửi Telegram**:

- Tick **Gửi Telegram** để bật/tắt gửi tự động.
- Chọn giờ bắt đầu, giờ kết thúc, số phút giữa các lần gửi và ngưỡng tăng/giảm. Bản tin gồm cổ phiếu mạnh, yếu và danh sách Quan Tâm; số lượng mã mạnh/yếu theo ngưỡng hiện tại hiển thị ngay cạnh ngưỡng.
- Chỉ gửi từ thứ 2 đến thứ 6 và khi VN-Index có dữ liệu phiên hôm nay; cuối tuần, ngày nghỉ không có phiên và khi không kiểm tra được phiên thì không gửi.
- App phải đang chạy trong khung giờ đã chọn. Lịch được kiểm tra bởi tác vụ nền trong app và lưu tại `data/board_settings.json`; lần gửi cuối được lưu ở `data/board_alert_state.json`.
- Log gửi tự động: `data\logs\server.log`. Nút **Xem trước** chỉ dựng tin, **Gửi tin nhắn** gửi ngay một lần.

Task Scheduler cũ **"PStock Board Alerts"** không còn gửi theo các trigger giờ cố định. Nếu vẫn còn task này, có thể disable hoặc xoá trong Task Scheduler. Chạy thủ công:

```bat
.venv\Scripts\python scripts\board_alerts.py --dry-run     :: xem trước, không gửi
.venv\Scripts\python scripts\board_alerts.py --once        :: gửi một lần (tôn trọng công tắc tự động)
.venv\Scripts\python scripts\board_alerts.py --force       :: gửi ngay, bỏ qua ngày nghỉ / công tắc
```

Tắt nhanh vẫn có thể dùng script:
```bat
auto_send.bat                 :: xem trạng thái (bấm đúp file này cũng được)
auto_send.bat off             :: tắt cả hai lịch;  auto_send.bat on  = bật lại
auto_send.bat off board       :: chỉ tắt bản tin bảng điện  (daily = chỉ BO/BD 15:05)
```
Trên Linux: `accstock auto`, `accstock auto off`, `accstock auto on daily`. Hoặc sửa tay `data/board_settings.json`
(`"auto_board": false` / `"auto_daily": false`).

## 4d. Ichimoku BO trong phiên (khung 30m / 60m) qua Telegram

Ở tab **Cổ phiếu** (đồ thị mặc định khung 60m), cùng hàng với các nút 5m … Tuần, sát lề phải:
- **Gửi BO 60m / Gửi BO 30m**: quét toàn bộ mã trong các bảng của Bảng điện (VN30 + các ngành + Quan Tâm) theo
  khung đang chọn, lấy nến **đã đóng** gần nhất, gửi Telegram danh sách mã **phá mây Ichimoku (BO)**, xếp theo Điểm
  từ cao tới thấp. Không gửi tín hiệu BD. Không có mã BO thì không gửi (vẫn hiện kết quả trên trang).
- **30m / 60m**: chọn khung cho cả nút gửi và chế độ tự động.
- ☐ **Tự động gửi BO** (mặc định tắt): gửi sau khi mỗi nến đóng 5 phút, các ngày giao dịch (app phải đang chạy):
  - 30m: 9:35, 10:05, 10:35, 11:05, 11:35, 13:35, 14:05, 14:35, 15:05
  - 60m: 10:05, 11:05, 11:35 (nến 11:00–11:30), 14:05, 15:05 (nến 14:00–14:45)

Một lần quét mất khoảng 20 giây; lần quét 30m đầu tiên lâu hơn (~1–2 phút) vì phải tải lịch sử nến 1 phút.

## 4e. Hỏi bot Telegram: /ck MãCK

Khi app đang chạy, gõ trong group (hoặc chat) đã khai báo ở `TELEGRAM_CHAT_ID`:
```
/ck FPT
```
Bot trả lời: giá, % thay đổi, KL, tham chiếu/trần/sàn, **Điểm** khung Ngày, Ichimoku (vị trí so với mây, Tenkan/Kijun,
Chikou, mây tương lai, Kijun 65/129, BO/BD), chu kỳ thời gian, MACD, RSI, KL so với MA20, kháng cự/hỗ trợ 20 phiên.
`/help` xem cú pháp. Chat không có trong `TELEGRAM_CHAT_ID` bị bỏ qua. Tắt: `TELEGRAM_COMMANDS=false` trong `telegram.env`.
Mỗi bot chỉ được **một** app nhận lệnh: nếu chạy app cả trên Windows và Linux với cùng token, chỉ một bên trả lời.

## 5. Cấu trúc

```
app/
  main.py                FastAPI app + dashboard
  api/                   market.py, analysis.py, ai.py, deps.py
  core/                  config.py (.env), security.py (API key, che giấu secret)
  services/              ssi_service.py (ssi-sdk REST), realtime_service.py (SSI stream), vietcap_service.py,
                         timeframes.py (khung + gộp nến), stock_service.py, claude_service.py,
                         priceboard_service.py + sectors.py (Bảng điện, ngành), board_settings.py (Quan Tâm,
                         ngưỡng, bật/tắt gửi tự động), telegram_service.py
  analysis/              ichimoku.py, macd.py, rsi.py, breakout.py, stock_scanner.py, strength.py (RS Rating), acc_strength.py (Xếp hạng ACC), wyckoff.py (Phân tích Wyckoff × Ichimoku),
                         daily_signals.py (BO/BD), board_alerts.py (bản tin mạnh/yếu)
  schemas/models.py      Pydantic models
  templates/dashboard.html
  static/js/             lightweight-charts 4.2.0 (đóng gói sẵn, chạy offline)
data/                  cache/, logs/, sectors.json, board_settings.json (gitignored)
scripts/               fetch_data, scan, check_connection, daily_signals, board_alerts, auto_send, telegram_chat_id
tests/
run.bat                Windows: tạo .venv, cài thư viện, chạy app
daily_signals.bat  board_alerts.bat   Task Scheduler (mục 4b, 4c)
auto_send.bat          bật/tắt gửi Telegram tự động không cần app
accstock               Linux launcher: ./accstock install, rồi accstock start | stop | restart | status | logs | auto
```

## 6. Lưu ý

- **SSI v3** (`ssi-sdk`): `SSI_CONSUMER_ID`/`SSI_CONSUMER_SECRET` được dùng làm `api_key`/`api_secret`. Dữ liệu thị trường và stream nến không cần OTP. Mỗi API key chỉ có **1 phiên** hoạt động, nên app dùng chung một phiên cho REST và stream. Đừng chạy app ở hai nơi cùng lúc với cùng một key.
- **Khung thời gian**: SSI có sẵn 5m/15m/1h/1D. **30m** được gộp từ 15m, **Tuần** gộp từ Ngày (nhãn = thứ Hai). Lịch sử trong ngày của SSI chỉ khoảng 7 tháng. Vietcap gộp 5m/15m/30m từ nến 1 phút.
- **Stream**: SSI chỉ phát nến `1m`/`5m`/tick. App đăng ký `1m` (tối đa 50 mã) cho mã đang mở trên biểu đồ. Nến trong ngày chỉ được ghi đè bằng dữ liệu stream khi stream đã chạy từ đầu nến đó. Với khung Ngày/Tuần, stream cập nhật giá đóng/cao/thấp của nến hiện tại (khối lượng lấy từ REST). Tắt bằng `SSI_STREAM_ENABLED=false`.
- Mạng công ty có kiểm tra SSL: app dùng kho chứng chỉ Windows (`truststore`).
- `SSI_PRIVATE_KEY` chỉ dùng cho FastConnect **Trading** (đặt lệnh). App này chỉ đọc dữ liệu nên không dùng key đó.
- Vietcap dùng API biểu đồ công khai `trading.vietcap.com.vn`, có thể thay đổi mà không báo trước. **Bảng điện** cũng lấy giá trong phiên từ API bảng giá công khai của Vietcap: SSI REST chỉ có số liệu cuối ngày, còn stream SSI giới hạn 50 mã.
- `CLAUDE_MODEL` có thể đổi sang model khác trong `.env`.
- Thông số Ichimoku Trịnh Phát được tổng hợp từ blog Trịnh Phát, bộ code Amibroker "Ichimoku kiểu VN 9-17-26-33-DG65" và chỉ báo TradingView "Ichimoku Trinh Phat" (Kijun 65 & 129). Mã nguồn gốc của các chỉ báo này không công khai; có thể chỉnh lại từng thông số qua `ICHI_PRESET=custom`.
- **Không commit file `.env`, `telegram.env`** (đã có trong `.gitignore`), cũng như `data/sectors.json`, `data/board_settings.json`.
- Kết quả chỉ mang tính tham khảo kỹ thuật, không phải khuyến nghị đầu tư.
