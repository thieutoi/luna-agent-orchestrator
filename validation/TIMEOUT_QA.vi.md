# QA timeout — bằng chứng và giới hạn (23/10/2026)

## Cơ chế

Task nhận timeout_seconds trong (0, 86400], mặc định None giữ chế độ inline cũ.
Khi cấu hình deadline, handler chạy trong multiprocessing spawn process riêng.
Deadline monotonic chung cho toàn tác vụ, gồm startup và mọi retry.
Khi quá hạn: terminate, join tối đa 2 giây, kill nếu cần, join thêm tối đa 2 giây.
Chỉ ghi TIMED_OUT/timed_out sau khi worker đã chết; timeout là terminal, không retry.
Process bị dừng thực tế, không chỉ ngừng chờ như Future.result(timeout).
Worker chỉ trả JSON qua file tạm, không nhận Orchestrator hay kết nối SQLite.
Snapshot riêng và conditional update chặn kết quả cũ ghi đè recovery/terminal state.

Lỗi giám sát/startup/cleanup khiến không thể khẳng định trạng thái worker:
IsolationError, giữ RUNNING, không retry. Nếu ghi audit timeout thất bại,
SQLite rollback cả thay đổi task; claim RUNNING vẫn chặn replay.

## Persistence / restart

Schema SQLite v1 được nâng lên v2 trong BEGIN IMMEDIATE transaction.
Task cũ được thêm timeout_seconds=None, tránh làm hỏng compare-and-swap.
Deadline monotonic không được tái dùng sau reboot. Restart giữ RUNNING, không tự
chạy lại. recover_interrupted chỉ dùng sau khi đã dừng/xác minh mọi executor/worker;
chuyển task gián đoạn thành FAILED với outcome unknown.

## Local QA thực tế

Windows/Python 3.12.10:
- 60 test PASS, 0 FAIL/ERROR/SKIP (45 nền + 15 test timeout).
- compileall PASS; demo gốc HELLO LUNA/completed PASS.
- Spawn worker thực, heartbeat dừng sau deadline, kể cả handler bỏ qua SIGTERM.
- Isolated success, retry rồi thành công, retry rồi timeout theo một budget.
- Chặn instance khác nhận task trong lúc worker sống.
- Timeout terminal lưu/đọc lại database, không replay.
- Kết quả thành công đến muộn bị deadline chặn.
- Race recovery + refresh không thể bị kết quả cũ ghi đè.
- Audit write failure rollback; lỗi terminate/supervisor không retry.
- Migration v1, config invalid, restart RUNNING được kiểm tra.
- Bộ test Telegram allowlist/private chat/commands/error sanitization đều PASS.

Log: validation/timeout-tests.txt.
CI sẽ được xác minh trên GitHub sau push; xem phần kết quả mới nhất trong PR #2.
Không xem kết quả local là bằng chứng Python 3.10/3.11 hay Linux.

## Giới hạn

- None là chế độ inline không có deadline, để tương thích handler cũ.
- Deadline mode cần callable top-level importable/picklable, không lambda/closure.
  Ứng dụng cần if __name__ == "__main__" guard.
- Cưỡng bức dừng bỏ qua finally/cleanup của handler; không rollback side effect.
- Không hỗ trợ handler tự tạo descendant process; không tự kill process tree.
- Đây không phải sandbox: worker vẫn có quyền tài khoản và environment.
- Supervisor crash có thể để lại worker sống; cần dừng worker trước recovery.
- Caller có thể trả về sau deadline do kill grace, OS scheduling và SQLite I/O.
- Audit chưa lưu Telegram actor; offset bot trong RAM; SQLite plaintext.
- Không test bot Telegram thật, không gửi tin nhắn, không deploy hay release.

Thiết kế đối chiếu với Python multiprocessing:
https://docs.python.org/3/library/multiprocessing.html#process-and-exceptions
