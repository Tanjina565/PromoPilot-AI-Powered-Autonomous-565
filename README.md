# PromoPilot – AI-Powered E-commerce Marketing Automation (SE331)

Python (Flask + SQLite) web app. Runs locally in VS Code.

## Run in VS Code
1. Install Python 3.10+ and open this folder in VS Code (File > Open Folder).
2. Open the terminal (Ctrl + `) and run:

   **Windows**
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
   **macOS / Linux**
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python app.py
   ```
3. Open http://127.0.0.1:5000

Demo logins: `owner@promopilot.com / owner123` (Business Owner), `admin@promopilot.com / admin123` (Admin).

## Optional: real LLM
Without a key, drafts use built-in templates (the fallback from the proposal). To use the Claude API:
```
set ANTHROPIC_API_KEY=your_key        (Windows)
export ANTHROPIC_API_KEY=your_key     (macOS/Linux)
```

## Requirement coverage
| ID | Where |
|---|---|
| M1-FR1 | Register, login, logout, roles (`/register`, `/login`) |
| M1-FR2 | Business profile + Facebook Page connection (`/businesses`) |
| M1-FR3 | Products CRUD + CSV import (`/businesses/<id>/products`) |
| M1-FR4 | Admin full access (every page) + `/admin/users` |
| M2-FR1 | `/generate` captions, regenerate, edit, A/B drafts |
| M2-FR2 | `/generate` description, hashtags, ad copy, SMS/email, English + Bangla |
| M2-FR3 | `/campaigns` content calendar + upcoming festival suggestions |
| M2-FR4 | `ml.py` best time + engagement level; `/admin/model` metrics |
| M3-FR1 | Approve / schedule / auto-publish, retries (`scheduler_loop` in `app.py`) |
| M3-FR2 | `/dashboard` |
| M3-FR3 | Recommendations on dashboard, `/posts` search + filters |
| M3-FR4 | `/report.pdf` |

## Notes for your report
- Facebook publishing is simulated (`scheduler_loop`). Replace that block with the Facebook Graph API call to go live.
- The ML model trains on synthetic data at startup. Replace `ml.train()` data with real post history.
- Passwords are hashed with Werkzeug (PBKDF2). Swap to bcrypt if your proposal requires it exactly.
- Only one platform (Facebook) in the MVP, as in the proposal.
