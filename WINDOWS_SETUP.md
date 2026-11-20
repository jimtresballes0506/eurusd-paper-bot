# Exness demo MT5 on your existing AWS Windows server

This uses the server you already have. It provisions no AWS resources and does not change its billing, firewall, login settings or existing bots. AWS server charges continue under your existing plan/credits.

The worker reads bid/ask prices from the desktop MT5 terminal. All trades are simulated in its own $10 USD ledger; **no orders appear in Exness/MT5**. The MT5 balance is never changed. The adapter refuses live accounts and pins the demo account, server and EUR/USD symbol. You do not need OANDA or another Railway worker for this route.

## Install on the server

1. Connect to the AWS Windows desktop using your existing Remote Desktop access.
2. Install/open the **Exness MT5 desktop terminal** there, using the download in your Exness Personal Area. Log in to your **demo** account inside MT5 and save its login in that terminal. Do not send passwords to ChatGPT or put them in this repository. This worker does not need algorithmic order permissions; you can leave trading via the external Python API disabled.
3. Install **64-bit Python 3.12** with its Python launcher from https://www.python.org/downloads/windows/ if it is not already installed. Python and MT5 must be available to the same Windows user.
4. Download this repository using **Code > Download ZIP**, extract it to a stable directory such as `C:\Bots\eurusd-paper-bot`. Keep the same directory for future starts: it contains the saved ledger. Do not install over another running copy.
5. In Windows PowerShell, run:

   ```powershell
   cd C:\Bots\eurusd-paper-bot
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
   ```

   This changes execution policy only for that child PowerShell process. The script creates a virtual environment, installs dependencies, asks for the terminal executable path, numeric demo login, exact demo server and EUR/USD symbol, checks MT5, and starts the `EURUSD-Paper-Bot` scheduled task. It requests no passwords. A common symbol is `EURUSDm`, but use the exact name in your own Market Watch. Find the terminal path through its shortcut's Properties rather than assuming the example path is correct.

   Existing `mt5-settings.json` is retained, and an existing task is not overwritten. If Task Scheduler registration is denied, run PowerShell as administrator **under the same Windows account** and retry. If you already have an environment configured, you can also run `.\.venv\Scripts\python.exe windows_worker.py` directly.

## Verify before leaving it

```powershell
Get-ScheduledTask -TaskName EURUSD-Paper-Bot
Get-Content .\logs\mt5.log -Tail 20 -Wait
```

Look for `MT5_CONNECTED` followed by reports with an advancing `last_quote_utc`. `MT5_DEMO_CHECK_OK` confirms the terminal/account check, **not** trading readiness; `fresh_quote: false` means it has no current price, e.g. during market closure. The feed retries disconnections and waits rather than inventing quotes. Setup errors stop it; the task attempts up to 10 restarts. Review logs if it stops repeatedly.

The strategy needs 50 contiguous completed five-minute bars, about 4 hours 10 minutes, before signals can qualify. It only opens positions on weekdays 07:00–16:00 UTC (15:00–midnight Manila). No trade is guaranteed. Quote gaps longer than 30 seconds reset warm-up. The process can remain on around the clock while forex markets are closed.

**Disconnect RDP instead of signing out.** This task runs in the logged-in Windows user's desktop session with MT5. Windows policy must allow disconnected sessions to remain logged in. After a reboot or sign-out, log back in to that same user; the task starts at logon. This configuration does **not** promise unattended recovery before login after a reboot, and does not enable automatic login or disable Windows updates.

## State, status and stopping

By default the worker saves `mt5-paper.db` on the Windows server's disk. It resumes that balance and positions on restart. Keep the database and its associated WAL files together; back up while stopped. A duplicate worker cannot acquire the same ledger's writer lock. Do not delete the database to reset losses.

```powershell
.\.venv\Scripts\python.exe bot.py status --db mt5-paper.db
Stop-ScheduledTask -TaskName EURUSD-Paper-Bot
# To prevent future starts at login as well:
Disable-ScheduledTask -TaskName EURUSD-Paper-Bot
# To resume later:
Enable-ScheduledTask -TaskName EURUSD-Paper-Bot
Start-ScheduledTask -TaskName EURUSD-Paper-Bot
```

An open simulated position remains in saved state when stopped. On a later quote after a data gap, it closes at that observed price, not a made-up historical stop price. There are no orders at the broker to close.

## Optional Tiger Cloud persistence

The worker supports the existing PostgreSQL `DATABASE_URL` environment variable. The Railway `${{worker.DATABASE_URL}}` reference does not work outside Railway: configure the actual Tiger Cloud URI privately on Windows.

Set `DATABASE_URL` in the Windows user's environment via the system environment-variable editor, then sign out and sign back in so Task Scheduler and PowerShell inherit it. Keep the same URI available for all runs/status checks. Use a TLS direct/session connection, not a transaction pooler. Then run the installer with `-RequirePostgres` to prevent falling back to SQLite if the URI is missing. If the task already exists, stop it and configure its Action to append `--require-postgres`, then restart it. Do not add the URI to task arguments or logs.

Choose storage **before the first trading run**. Local and PostgreSQL ledgers are independent; changing storage does not migrate state. If an existing database has an OANDA or different MT5 ledger, the adapter refuses to mix feeds. Existing crypto tables are untouched: this bot uses `eurusd_state` and `eurusd_events` and a dedicated advisory lock.

## Simulation limits

The original config permits whole EUR units, minimum one. This is a theoretical $10 experiment, **not a reproduction of Exness minimum lot sizes**. `MT5_CONNECTED` and `--check` report the real symbol's minimum and step in EUR units. To test broker-compatible sizing, set `min_units` and `unit_step` in a separate config/ledger to those values. With a $10 balance, 1% risk and simulated 30:1 leverage, real broker minimums may prevent every trade. Do not increase risk just to force trades. Leverage, commissions and margin rules remain modeled assumptions.

The adapter samples the latest MT5 quote twice a second, not every market tick. Stop fills are simulated at observed quotes plus configured slippage. Historical warm-up/backfills and real order execution are intentionally absent. Offline tests use a fake MT5 interface; an actual Windows/Exness session, Windows file locking, Task Scheduler and Tiger database connectivity still need verification on your server.

Official API: https://www.mql5.com/en/docs/python_metatrader5
