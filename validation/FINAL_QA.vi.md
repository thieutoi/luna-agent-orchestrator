# FINAL QA — PR #2 (23/10/2026)

## Quyết định

**Giữ PR #2 ở Draft. Chưa đạt toàn bộ yêu cầu bắt buộc: thiếu timeout cho handler.**
Không merge, không deploy bot thật, không release, không tạo PR mới.

PR: https://github.com/thieutoi/luna-agent-orchestrator/pull/2
Nhánh: feature/v0.2.0
Commit mã đã chạy CI: d8194869e808c7bc315234d8c0a75c42a383cb60

## Kiểm thử đã chạy thực tế

- Chạy lại nguyên suite nền 41 test trên Windows/Python 3.12.10: 41 PASS.
- Thêm 4 test QA, suite cuối: 45 PASS, 0 FAIL, 0 ERROR, 0 SKIP.
- compileall và demo gốc: PASS. Demo in HELLO LUNA, trạng thái completed.
- SQLite: task/audit nguyên tử, rollback khi ghi audit thất bại, khôi phục sau
  mở lại database và sau crash tiến trình con, approval/rejection và attempt counts.
- Chống chạy trùng: handler gọi lồng nhau, hai thread và hai instance cùng SQLite,
  conditional update stale bị chặn; failed/completed/rejected không thực thi lại.
- Retry: mặc định không retry, giới hạn 0–10, retry thành công và cạn budget,
  không retry trước duyệt; tác vụ blocking giữ running và không nhận lần execute khác.

## Timeout: FAIL yêu cầu tính năng

Handler chạy đồng bộ và không có execution deadline/cancellation.
Test mới xác nhận handler blocking giữ RUNNING cho tới khi được cho phép kết thúc;
test PASS này chỉ mô tả giới hạn, không chứng minh có timeout.

SQLite connect timeout=10, Telegram long-poll timeout=25, HTTP timeout=40 và
CI job timeout=10 phút KHÔNG phải timeout của Agent.
HTTP timeout đã test bằng mock: lỗi được che thông tin URL/token.
Cần thiết kế timeout có thể dừng executor an toàn, giữ trạng thái và ngăn retry
chồng lên handler cũ trước khi chuyển Ready for review. Không thêm cơ chế thread
timeout chỉ ngừng chờ trong khi handler vẫn chạy và có thể tạo side effect.

## CI GitHub Actions: đã chạy thật, PASS 6/6

Trước QA chưa có workflow hay PR workflow run.
Đã bổ sung .github/workflows/python-qa.yml, pin action bằng SHA từ repository chính
thức, contents:read, checkout không lưu credential, không cài dependency bên thứ ba.

Run: https://github.com/thieutoi/luna-agent-orchestrator/actions/runs/39866709690
Run trigger pull_request, kiểm thử commit d8194869e808c7bc315234d8c0a75c42a383cb60.

| Python | Ubuntu | Windows |
|---|---|---|
| 3.10 | PASS | PASS |
| 3.11 | PASS | PASS |
| 3.12 | PASS | PASS |

Cả 6 job có conclusion=success; các bước Compile, Unit and regression tests,
Demo thành công. Đã đối chiếu log Ubuntu 3.10 có dòng Ran 45 tests và OK.
Máy local chỉ có Python 3.12; kết quả 3.10/3.11 là từ runner GitHub, không phải local.
Các commit tài liệu sau commit trên không đổi mã runtime/test/workflow.

## Telegram / bảo mật

- Test đủ /start, /status, /tasks, /approve, /reject; approve không chạy handler.
- User ID phải là int dương trong allowlist; bool/string bị từ chối.
- Private chat phải khớp sender; bot, group và malformed update không có side effect.
- Reply không chứa payload/result; lỗi HTTP và timeout không in token-bearing URL.
- Lỗi xác nhận: frozenset gộp True với 1 và 1.0 với 1 trước khi kiểm tra kiểu,
  khiến cấu hình allowlist chứa kiểu không hợp lệ được chấp nhận.
  Regression thất bại trước sửa với 3 subcase; đã kiểm tra kiểu trước deduplication.
  Đây là lỗi validation cấu hình, không phải bằng chứng user lạ bypass qua Telegram.
- Quét nội dung file của 14 commit reachable, 37 blob: không có mẫu credential,
  private key, token, email cá nhân hay đường dẫn người dùng. Xem final-qa-security.json.
  Không quét giá trị GitHub Secrets hay dữ liệu ngoài repo; không cam kết phát hiện
  tuyệt đối mọi dạng bí mật. Metadata tác giả commit theo danh tính chủ sở hữu cung cấp.
- Không truy cập repository riêng tư bị loại trừ, không đọc token bot có sẵn.
- Không chạy bot Telegram thật hoặc gửi tin nhắn ra ngoài; Telegram network dùng mock.
- SQLite plaintext, trusted mutable API, retry side effect, offset trong RAM và
  thiếu actor ID trong audit vẫn là giới hạn đã công bố.

## Bằng chứng và thay đổi

- validation/final-qa-tests.txt: suite 45 test.
- validation/final-qa-regression-before.txt: lỗi allowlist trước sửa; đã che đường dẫn.
- validation/final-qa-security.json: phạm vi/kết quả quét nội dung Git.
- tests/test_final_qa.py: 4 test QA bổ sung.
- luna_orchestrator/telegram.py: sửa validation allowlist.
- .github/workflows/python-qa.yml: matrix CI thực tế.
- README.md: nêu rõ điều kiện Draft và phân biệt timeout handler/network.
