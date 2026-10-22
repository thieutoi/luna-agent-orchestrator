# Báo cáo kiểm thử ban đầu — 23/10/2026

Repository: https://github.com/thieutoi/luna-agent-orchestrator
Mốc main: 8a806be. Nhánh làm việc: test/initial-validation.
Môi trường đã kiểm chứng: Windows, Python 3.12.10, venv .venv, không cài dependency bên thứ ba.

## Kết quả

| Lần chạy toàn bộ suite | Tổng | PASS | FAIL | ERROR | SKIP |
|---|---:|---:|---:|---:|---:|
| Test gốc trước sửa | 4 | 4 | 0 | 0 | 0 |
| Thêm regression, trước sửa | 15 | 13 | 2 | 0 | 0 |
| Sau sửa | 15 | 15 | 0 | 0 | 0 |

Có 15 test độc lập ở suite cuối (11 test mới); tổng 34 lượt test trong ba lần chạy.
Compileall và import tất cả module của package thành công.
Log nguyên trạng về nội dung kết quả ở validation/*.txt; đường dẫn cá nhân trong traceback đã được thay bằng <REPO>.

## Lỗi xác nhận và sửa

Cùng một nguyên nhân gây hai lỗi kiểm thử: execute giữ trạng thái pending trong khi
handler đang chạy, cho phép gọi lồng nhau hoặc gọi đồng thời thực thi lại cùng task.
Sửa: thêm RUNNING và Lock bảo vệ nguyên tử bước kiểm tra/nhận task; approve dùng cùng
khóa để bảo vệ chuyển trạng thái. Không giữ khóa khi chạy handler.
Hai regression test thất bại trước sửa và thành công sau sửa.
Đây không phải cam kết thread safety toàn bộ registry hay chống sửa trực tiếp dữ liệu.

Các test mới bao phủ: đăng ký hợp lệ/không hợp lệ, chuyển payload, approval không có
side effect trước duyệt, audit đúng thứ tự, chặn chạy lại sau hoàn thành/thất bại,
worker/task không tồn tại, approval sai trạng thái/lặp, exception và lỗi chuyển kết
quả sang chuỗi, gọi lồng nhau và đồng thời.

## Demo thực tế

Trước và sau sửa, exit code 0:

    Submitted: awaiting_approval
    Result: HELLO LUNA
    Status: completed

Demo chỉ dùng handler echo cục bộ; chưa xác minh LLM/Telegram hoặc dịch vụ bên ngoài.

## Bảo mật

Đã đọc toàn bộ file được theo dõi và quét mẫu credential, private key, token,
đường dẫn người dùng, eval/exec/subprocess trong lịch sử Git có thể truy cập:
9 commit, 9 blob khác nhau, không có mẫu trùng. Xem security-scan.json.
Không phát hiện dependency bên thứ ba hay đường thực thi shell/eval từ payload.
Quét mẫu không chứng minh tuyệt đối không có bí mật; không kiểm tra remote refs
không được fetch, nội dung bị xóa ngoài lịch sử, GitHub secrets hay dữ liệu dịch vụ.

Approval chỉ là API tin cậy trong cùng tiến trình: người có quyền gọi approve hoặc
sửa task/dictionary có thể thay đổi quyết định/nội dung. Đây là giới hạn đã được
SECURITY.md nêu, không coi là xác thực người dùng. Handler có đầy đủ quyền tiến trình,
không chạy mã không tin cậy. Exception text lưu nguyên văn vào result có thể chứa
dữ liệu nhạy cảm do handler đưa vào. Audit nằm trong RAM, có thể sửa, không bền vững.
Đã bổ sung tài liệu về các giới hạn này; chưa triển khai tính năng v0.2.0.
Không truy cập, sao chép hay sử dụng repository riêng tư bị loại trừ.

## File thay đổi

- luna_orchestrator/core.py: chặn thực thi trùng khi đang chạy.
- tests/test_validation.py: 11 test mới.
- README.md: mô tả trạng thái running và phạm vi bảo vệ.
- SECURITY.md: làm rõ trust boundary.
- validation/REPORT.vi.md: báo cáo này.
- validation/before-compile.txt, before-tests.txt, before-demo.txt.
- validation/regression-before.txt.
- validation/after-compile.txt, after-tests.txt, after-demo.txt, imports.txt.
- validation/security-scan.json.

## GitHub / Pull Request

Đã cấu hình danh tính Git cục bộ theo thông tin chủ sở hữu cung cấp.
Đã rà soát toàn bộ staged diff, không phát hiện credential hay dữ liệu cá nhân
trong nội dung file. Git commit có metadata tác giả theo danh tính được cung cấp.

Chạy lại compileall: thành công. Suite 15 test: 15 PASS, 0 FAIL/ERROR/SKIP.
Demo: awaiting_approval -> HELLO LUNA -> completed.
Lần chạy lại này bổ sung 15 lượt test vào 34 lượt đã ghi ở phần kiểm thử ban đầu.

Commit mã đã kiểm thử: c41f96fc0f60af4929d5f7d78bec9f7e7b02767e.
Đã push nhánh test/initial-validation bằng Git HTTPS / Git Credential Manager.
Pull Request vào main: https://github.com/thieutoi/luna-agent-orchestrator/pull/1
PR đã tạo, chưa merge. Không force push, không tạo release, không sửa main.
GitHub CLI không được cài thêm; PR được tạo qua GitHub connector.
Bản cập nhật báo cáo này được lưu bằng commit tài liệu riêng.

## Ưu tiên v0.2.0 (chỉ đề xuất)

1. GitHub Actions CI cho Python 3.10 và các bản ổn định mới trên Windows/Linux.
2. Human approval: xác thực người duyệt, gắn quyết định với nội dung bất biến, audit.
3. SQLite persistence: giao dịch nhận task nguyên tử, phục hồi task running sau crash.
4. Retry và timeout: idempotency, giới hạn lần thử, phân loại lỗi; timeout không tự
   dừng được Python handler đồng bộ đang chạy trong thread.
5. Logging và monitoring: log có cấu trúc, che bí mật, event trạng thái, metrics.
6. Telegram adapter: allowlist, phân quyền lệnh, quản lý token qua môi trường.
7. Scheduling: timezone rõ ràng, chống chạy trùng, chính sách job bị lỡ.

Chưa kiểm chứng Python 3.10/3.11 hoặc Linux, crash recovery, tải dài hạn, nhiều tiến
trình, handler không tin cậy hay người dùng đối kháng. Chưa triển khai mục roadmap nào.
