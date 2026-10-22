# Telegram setup — Windows and Linux

Uses the official [Telegram Bot API](https://core.telegram.org/bots/api).
Only the explicit telegram command contacts Telegram; demo/configure/doctor do not.

## Prepare

Follow README's venv/install/demo steps. Use a separate bot/database, not business
automation credentials. Run luna configure. Supply your numeric Telegram account
ID, not username/phone/chat title, obtained through a trusted method you control.
Do not share tokens with third-party ID lookup sites.
LUNA_TELEGRAM_USER_IDS (comma-separated integers) can override configured IDs.

Create your bot with Telegram's official @BotFather. Start a private conversation.
Keep the token local. Accounts outside the allowlist receive no replies.

## Set token without displaying it

Windows PowerShell:

    $localSecret = Read-Host "Bot token" -AsSecureString
    $env:LUNA_TELEGRAM_TOKEN = [System.Net.NetworkCredential]::new("", $localSecret).Password
    .venv\Scripts\luna.exe doctor
    .venv\Scripts\luna.exe telegram

Linux Bash:

    read -rsp "Bot token: " LUNA_TELEGRAM_TOKEN
    echo
    export LUNA_TELEGRAM_TOKEN
    .venv/bin/luna doctor
    .venv/bin/luna telegram

Never paste tokens into chat, arguments, Git, issues or screenshots.
Do not run token-bearing curl URLs in shell history. Doctor reports presence only,
not successful authentication. Ctrl+C stops polling. Afterwards remove the variable:

Windows: Remove-Item Env:LUNA_TELEGRAM_TOKEN; Remove-Variable localSecret
Linux: unset LUNA_TELEGRAM_TOKEN

## Owner-run live verification

Send /start, /system, /tasks, /logs from an allowed private account.
Send /status from another account outside the allowlist: expect no reply.
For approval testing, submit a sensitive echo task with a trusted local application
using the same database. /approve only changes state to pending. Use a separate
executor to run it; arbitrary chat-supplied code is never accepted.

Project QA uses mocked transport, no real token. These live steps are owner-operated.
No command automatically removes webhooks or discards pending updates.
An existing webhook must be removed through an owner-controlled workflow before
polling. Never run two pollers per token, even on different machines/databases.

## Troubleshooting

- 401/403/409: stop and inspect token, webhook and other pollers; no automatic retry.
- Network/timeout/5xx: bounded exponential reconnect, stopping after six consecutive
  failed polling iterations. Success resets the count. Correct the cause then restart.
- 429: honor valid retry_after up to an hour; Ctrl+C can interrupt the wait.
- Missing reply after disconnect: decision may have committed. Inspect /tasks and
  /logs before retrying a command. Reply/checkpoint/decision are not one transaction.
- Dated approval/rejection commands older than five minutes are rejected.
- Errors never print raw API response bodies or token-bearing URLs.
- Restart never automatically replays a task left running after a supervisor crash.

## Optional unattended operation (manual templates)

Windows Task Scheduler: program is venv luna.exe; arguments are
telegram --config FULL_CONFIG_PATH. Set Start in explicitly. Use a dedicated account.
Never put token in task arguments. Deliver its environment/secret locally for that
account; a variable in your shell does not transfer to another task account.

Linux: adapt examples/luna-telegram.service with your actual account/paths.
Keep EnvironmentFile readable only by the service account/root; set the token there
locally and never commit it. Use restart delays to avoid service restart storms.
Stop all executors and verify surviving workers before maintenance recovery.

No services/accounts/tasks are installed automatically. Restrict directory ACLs
on Windows and file modes on Linux. Do not attach configs/databases to issue reports.
