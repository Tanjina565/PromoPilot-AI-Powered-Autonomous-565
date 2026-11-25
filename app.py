import os, io, csv, random, sqlite3, threading, time
from datetime import datetime, timedelta, date
from functools import wraps
from collections import defaultdict
from flask import (Flask, g, render_template, request, redirect, url_for, session, flash, abort, send_file)
from werkzeug.security import generate_password_hash, check_password_hash
import ai, ml

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "promopilot.db")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-in-production")
FMT = "%Y-%m-%dT%H:%M"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password_hash TEXT,
  role TEXT DEFAULT 'owner', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS businesses(id INTEGER PRIMARY KEY, owner_id INTEGER, name TEXT, category TEXT, audience TEXT,
  tone TEXT DEFAULT 'friendly', language TEXT DEFAULT 'English', contact TEXT, fb_page TEXT DEFAULT '', auto_publish INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, business_id INTEGER, name TEXT, description TEXT, price REAL,
  category TEXT, image TEXT, in_stock INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS campaigns(id INTEGER PRIMARY KEY, business_id INTEGER, name TEXT, goal TEXT, start TEXT, end TEXT);
CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, business_id INTEGER, product_id INTEGER, campaign_id INTEGER,
  platform TEXT DEFAULT 'Facebook', kind TEXT DEFAULT 'caption', content TEXT, hashtags TEXT, variant TEXT DEFAULT 'A',
  status TEXT DEFAULT 'draft', scheduled_at TEXT, published_at TEXT, error TEXT, retries INTEGER DEFAULT 0,
  approved_by TEXT, predicted_level TEXT, source TEXT, created_at TEXT, reach INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
  comments INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0);
"""

# ---------- database helpers ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, args=(), one=False):
    cur = db().execute(sql, args); rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows

def ex(sql, args=()):
    cur = db().execute(sql, args); db().commit(); return cur.lastrowid

def now(): return datetime.now().strftime(FMT)

# ---------- auth / access control ----------
RATE = defaultdict(list)

@app.before_request
def load_user():
    if request.method == "POST":  # simple rate limit: 90 POSTs / minute / IP
        t = time.time(); RATE[request.remote_addr] = [x for x in RATE[request.remote_addr] if t - x < 60] + [t]
        if len(RATE[request.remote_addr]) > 90: abort(429)
    g.user = q("SELECT * FROM users WHERE id=?", (session.get("uid"),), one=True) if session.get("uid") else None
    if g.user and not g.user["active"]:
        session.clear(); g.user = None

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user: return redirect(url_for("login"))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if g.user["role"] != "admin": abort(403)
        return f(*a, **k)
    return w

def is_admin(): return g.user and g.user["role"] == "admin"

def my_businesses():
    return q("SELECT * FROM businesses ORDER BY name") if is_admin() else \
           q("SELECT * FROM businesses WHERE owner_id=? ORDER BY name", (g.user["id"],))

def get_business(bid):
    b = q("SELECT * FROM businesses WHERE id=?", (bid,), one=True)
    if not b or (not is_admin() and b["owner_id"] != g.user["id"]): abort(404)
    return b

def get_post(pid):
    p = q("SELECT * FROM posts WHERE id=?", (pid,), one=True)
    if not p: abort(404)
    get_business(p["business_id"]); return p

def get_product(pid):
    p = q("SELECT * FROM products WHERE id=?", (pid,), one=True)
    if not p: abort(404)
    get_business(p["business_id"]); return p

@app.context_processor
def inject():
    return {"me": g.get("user"), "FESTIVALS": ai.FESTIVALS, "has_llm": bool(ai.API_KEY)}

@app.route("/")
def home(): return redirect(url_for("dashboard") if session.get("uid") else url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form; email = f["email"].strip().lower()
        if len(f["password"]) < 6: flash("Password must have at least 6 characters.", "err")
        elif q("SELECT 1 FROM users WHERE email=?", (email,), one=True): flash("That email is already registered.", "err")
        else:
            uid = ex("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'owner')",
                     (f["name"].strip(), email, generate_password_hash(f["password"])))
            session["uid"] = uid; return redirect(url_for("business_new"))
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q("SELECT * FROM users WHERE email=?", (request.form["email"].strip().lower(),), one=True)
        if not u or not check_password_hash(u["password_hash"], request.form["password"]): flash("Wrong email or password.", "err")
        elif not u["active"]: flash("This account is suspended. Contact an admin.", "err")
        else: session["uid"] = u["id"]; return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

# ---------- businesses & products (Module 1) ----------
@app.route("/businesses")
@login_required
def businesses(): return render_template("businesses.html", items=my_businesses())

@app.route("/businesses/new", methods=["GET", "POST"])
@app.route("/businesses/<int:bid>/edit", methods=["GET", "POST"])
@login_required
def business_new(bid=None):
    b = get_business(bid) if bid else None
    if request.method == "POST":
        f = request.form
        vals = (f["name"], f["category"], f["audience"], f["tone"], f["language"], f["contact"])
        if b: ex("UPDATE businesses SET name=?,category=?,audience=?,tone=?,language=?,contact=? WHERE id=?", vals + (b["id"],))
        else: bid = ex("INSERT INTO businesses(name,category,audience,tone,language,contact,owner_id) VALUES(?,?,?,?,?,?,?)", vals + (g.user["id"],))
        flash("Business saved.", "ok"); return redirect(url_for("products", bid=b["id"] if b else bid))
    return render_template("business_form.html", b=b)

@app.post("/businesses/<int:bid>/delete")
@login_required
def business_delete(bid):
    get_business(bid)
    for t in ("posts", "products", "campaigns"): ex(f"DELETE FROM {t} WHERE business_id=?", (bid,))
    ex("DELETE FROM businesses WHERE id=?", (bid,)); flash("Business deleted.", "ok"); return redirect(url_for("businesses"))

@app.post("/businesses/<int:bid>/settings")
@login_required
def business_settings(bid):
    get_business(bid)
    ex("UPDATE businesses SET fb_page=?, auto_publish=? WHERE id=?",
       (request.form.get("fb_page", "").strip(), 1 if request.form.get("auto_publish") else 0, bid))
    flash("Publishing settings saved.", "ok"); return redirect(request.referrer or url_for("products", bid=bid))

@app.route("/businesses/<int:bid>/products")
@login_required
def products(bid):
    return render_template("products.html", b=get_business(bid), items=q("SELECT * FROM products WHERE business_id=? ORDER BY id DESC", (bid,)))

@app.route("/businesses/<int:bid>/products/new", methods=["GET", "POST"])
@app.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def product_form(bid=None, pid=None):
    p = get_product(pid) if pid else None
    b = get_business(p["business_id"] if p else bid)
    if request.method == "POST":
        f = request.form; vals = (f["name"], f["description"], float(f["price"] or 0), f["category"], f["image"], 1 if f.get("in_stock") else 0)
        if p: ex("UPDATE products SET name=?,description=?,price=?,category=?,image=?,in_stock=? WHERE id=?", vals + (p["id"],))
        else: ex("INSERT INTO products(name,description,price,category,image,in_stock,business_id) VALUES(?,?,?,?,?,?,?)", vals + (b["id"],))
        flash("Product saved.", "ok"); return redirect(url_for("products", bid=b["id"]))
    return render_template("product_form.html", b=b, p=p)

@app.post("/products/<int:pid>/delete")
@login_required
def product_delete(pid):
    p = get_product(pid); ex("DELETE FROM products WHERE id=?", (pid,)); flash("Product deleted.", "ok")
    return redirect(url_for("products", bid=p["business_id"]))

@app.post("/businesses/<int:bid>/import")
@login_required
def product_import(bid):
    get_business(bid); file = request.files.get("csv"); n = 0
    if not file: flash("Choose a CSV file first.", "err"); return redirect(url_for("products", bid=bid))
    for r in csv.DictReader(io.StringIO(file.read().decode("utf-8-sig"))):
        if not (r.get("name") or "").strip(): continue
        try: price = float(r.get("price") or 0)
        except ValueError: price = 0
        ex("INSERT INTO products(business_id,name,description,price,category,image,in_stock) VALUES(?,?,?,?,?,?,1)",
           (bid, r["name"].strip(), r.get("description", ""), price, r.get("category", ""), r.get("image", "")))
        n += 1
    flash(f"Imported {n} products.", "ok"); return redirect(url_for("products", bid=bid))

# ---------- AI content & campaigns (Module 2) ----------
def make_post(b, p, platform, tone, goal, lang, kind, variant, when=None, campaign_id=None):
    out = ai.generate(b, p, platform, tone, goal, lang, kind, variant)
    if when:
        when, level = ml.best_time_on(when, out["content"], out["hashtags"])
    else:
        when, level = ml.best_time(out["content"], out["hashtags"])
    return ex("""INSERT INTO posts(business_id,product_id,campaign_id,platform,kind,content,hashtags,variant,status,scheduled_at,
                 predicted_level,source,created_at) VALUES(?,?,?,?,?,?,?,?, 'draft',?,?,?,?)""",
              (b["id"], p["id"], campaign_id, platform, kind, out["content"], out["hashtags"], "AB"[variant % 2],
               when.strftime(FMT), level, out["source"], now())), out["removed"]

@app.route("/generate", methods=["GET", "POST"])
@login_required
def generate():
    biz = my_businesses()
    if request.method == "POST":
        f = request.form; b = get_business(int(f["business_id"])); p = get_product(int(f["product_id"]))
        if p["business_id"] != b["id"]: abort(400)
        removed = []
        for v in range(int(f.get("variants", 1))):
            _, rem = make_post(b, p, f["platform"], f["tone"], f["goal"], f["language"], f["kind"], v); removed += rem
        if removed: flash("Safety check removed unsupported claims: " + ", ".join(removed), "ok")
        flash("Draft ready. Review it, then approve.", "ok"); return redirect(url_for("posts", status="draft"))
    prods = q("SELECT * FROM products WHERE business_id IN (%s) ORDER BY name" % ",".join(str(b["id"]) for b in biz) or "0") if biz else []
    return render_template("generate.html", biz=biz, prods=prods, sel=request.args.get("product_id"))

@app.route("/campaigns")
@login_required
def campaigns():
    ids = [b["id"] for b in my_businesses()] or [0]
    rows = q("SELECT c.*, b.name bname, (SELECT COUNT(*) FROM posts WHERE campaign_id=c.id) n FROM campaigns c JOIN businesses b ON b.id=c.business_id "
             "WHERE c.business_id IN (%s) ORDER BY c.id DESC" % ",".join(map(str, ids)))
    return render_template("campaigns.html", items=rows, biz=my_businesses(), today=date.today().isoformat(),
                           prods=q("SELECT * FROM products WHERE business_id IN (%s)" % ",".join(map(str, ids))),
                           upcoming=[f for f in ai.FESTIVALS if f[1] >= date.today().isoformat()][:6])

@app.post("/campaigns/new")
@login_required
def campaign_new():
    f = request.form; b = get_business(int(f["business_id"]))
    pids = [int(x) for x in f.getlist("product_ids")]
    prods = [get_product(i) for i in pids if get_product(i)["business_id"] == b["id"]]
    start, end = datetime.strptime(f["start"], "%Y-%m-%d"), datetime.strptime(f["end"], "%Y-%m-%d")
    if not prods or end < start: flash("Pick at least one product and a valid date range.", "err"); return redirect(url_for("campaigns"))
    cid = ex("INSERT INTO campaigns(business_id,name,goal,start,end) VALUES(?,?,?,?,?)", (b["id"], f["name"], f["goal"], f["start"], f["end"]))
    day, i = start, 0
    while day <= end and i < 15:  # one post slot every 2 days, max 15 slots
        make_post(b, prods[i % len(prods)], "Facebook", b["tone"], f["goal"], b["language"], "caption", i, when=day, campaign_id=cid)
        day += timedelta(days=2); i += 1
    flash(f"Campaign calendar created with {i} draft posts.", "ok"); return redirect(url_for("campaign_view", cid=cid))

@app.route("/campaigns/<int:cid>")
@login_required
def campaign_view(cid):
    c = q("SELECT * FROM campaigns WHERE id=?", (cid,), one=True) or abort(404); b = get_business(c["business_id"])
    rows = q("SELECT p.*, pr.name pname FROM posts p LEFT JOIN products pr ON pr.id=p.product_id WHERE campaign_id=? ORDER BY scheduled_at", (cid,))
    return render_template("campaign_view.html", c=c, b=b, rows=rows)

# ---------- posts: review, approve, schedule, publish (Module 3) ----------
@app.route("/posts")
@login_required
def posts():
    ids = [b["id"] for b in my_businesses()] or [0]
    where, args = ["p.business_id IN (%s)" % ",".join(map(str, ids))], []
    for key, col in (("status", "p.status"), ("platform", "p.platform"), ("product_id", "p.product_id"), ("campaign_id", "p.campaign_id")):
        if request.args.get(key): where.append(f"{col}=?"); args.append(request.args[key])
    if request.args.get("q"): where.append("(p.content LIKE ? OR p.hashtags LIKE ?)"); args += ["%" + request.args["q"] + "%"] * 2
    if request.args.get("from"): where.append("substr(p.scheduled_at,1,10)>=?"); args.append(request.args["from"])
    if request.args.get("to"): where.append("substr(p.scheduled_at,1,10)<=?"); args.append(request.args["to"])
    rows = q("SELECT p.*, pr.name pname, b.name bname FROM posts p LEFT JOIN products pr ON pr.id=p.product_id JOIN businesses b ON b.id=p.business_id "
             "WHERE " + " AND ".join(where) + " ORDER BY p.id DESC LIMIT 200", args)
    return render_template("posts.html", rows=rows, f=request.args, prods=q("SELECT id,name FROM products WHERE business_id IN (%s)" % ",".join(map(str, ids))),
                           camps=q("SELECT id,name FROM campaigns WHERE business_id IN (%s)" % ",".join(map(str, ids))))

@app.route("/posts/<int:pid>", methods=["GET", "POST"])
@login_required
def post_edit(pid):
    p = get_post(pid); b = get_business(p["business_id"])
    if request.method == "POST":
        if p["status"] == "published": abort(400)
        content, removed = ai.safety_check(request.form["content"])
        ex("UPDATE posts SET content=?, hashtags=?, scheduled_at=? WHERE id=?", (content, request.form["hashtags"], request.form["scheduled_at"], pid))
        flash("Saved." + (" Unsupported claims were removed: " + ", ".join(removed) if removed else ""), "ok"); return redirect(url_for("post_edit", pid=pid))
    return render_template("post_edit.html", p=p, b=b)

@app.post("/posts/<int:pid>/regenerate")
@login_required
def post_regen(pid):
    p = get_post(pid); b = get_business(p["business_id"]); pr = get_product(p["product_id"])
    out = ai.generate(b, pr, p["platform"], b["tone"], "sale", b["language"], p["kind"], random.randint(0, 5))
    ex("UPDATE posts SET content=?, hashtags=?, source=? WHERE id=?", (out["content"], out["hashtags"], out["source"], pid))
    return redirect(url_for("post_edit", pid=pid))

@app.post("/posts/<int:pid>/<action>")
@login_required
def post_action(pid, action):
    p = get_post(pid); b = get_business(p["business_id"])
    if action == "delete" and p["status"] != "published": ex("DELETE FROM posts WHERE id=?", (pid,))
    elif action == "approve" and p["status"] == "draft":
        ex("UPDATE posts SET status=?, approved_by=? WHERE id=?",
           ("scheduled" if b["auto_publish"] else "approved", f"{g.user['name']} ({g.user['role']}) at {now()}", pid))
        flash("Approved." + (" Auto-publish is on, so it is scheduled." if b["auto_publish"] else " Schedule it when you are ready."), "ok")
    elif action == "schedule" and p["status"] == "approved": ex("UPDATE posts SET status='scheduled' WHERE id=?", (pid,))
    elif action == "publish" and p["status"] in ("approved", "scheduled", "failed"):
        ex("UPDATE posts SET status='scheduled', scheduled_at=?, retries=0, error=NULL WHERE id=?", (now(), pid)); flash("Publishing in a few seconds.", "ok")
    return redirect(request.referrer or url_for("posts"))

# ---------- dashboard, recommendations, reports ----------
def scope_ids():
    bid = request.args.get("business_id")
    if bid: get_business(int(bid)); return [int(bid)]
    return [b["id"] for b in my_businesses()] or [0]

def stats(ids):
    rows = q("SELECT * FROM posts WHERE business_id IN (%s)" % ",".join(map(str, ids)))
    pub = [r for r in rows if r["status"] == "published"]
    t = {k: sum(r[k] for r in pub) for k in ("reach", "likes", "comments", "clicks")}
    t["rate"] = round(100 * (t["likes"] + t["comments"] + t["clicks"]) / t["reach"], 1) if t["reach"] else 0
    by_status = defaultdict(int)
    for r in rows: by_status[r["status"]] += 1
    trend = defaultdict(int)
    for r in pub: trend[(r["published_at"] or "")[:10]] += r["likes"] + r["comments"] + r["clicks"]
    return rows, pub, t, dict(by_status), sorted(trend.items())[-10:]

def findings(pub):
    out = []
    def best(keyf, label):
        grp = defaultdict(list)
        for r in pub:
            if r["reach"]: grp[keyf(r)].append((r["likes"] + r["comments"] + r["clicks"]) / r["reach"])
        if len(grp) > 0:
            k, v = max(grp.items(), key=lambda kv: sum(kv[1]) / len(kv[1])); return k, round(100 * sum(v) / len(v), 1)
    h = best(lambda r: int(r["published_at"][11:13]), "hour")
    if h: out.append(f"Posts published around {h[0]}:00 get the best engagement ({h[1]}% on average). Schedule more posts near that time.")
    k = best(lambda r: r["kind"], "kind")
    if k: out.append(f"'{k[0]}' content performs best ({k[1]}% engagement). Make more of it.")
    v = best(lambda r: r["variant"], "variant")
    if v: out.append(f"Caption style {v[0]} beats the other variant ({v[1]}%). Use that style as your default.")
    return out

@app.route("/dashboard")
@login_required
def dashboard():
    ids = scope_ids(); rows, pub, t, by_status, trend = stats(ids)
    users = q("SELECT COUNT(*) c FROM users", one=True)["c"] if is_admin() else None
    return render_template("dashboard.html", t=t, by_status=by_status, trend=trend, recs=ai.recommend(findings(pub)), biz=my_businesses(),
                           sel=request.args.get("business_id", ""), total=len(rows), users=users)

@app.route("/report.pdf")
@login_required
def report():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    ids = scope_ids(); rows, pub, t, by_status, _ = stats(ids); st = getSampleStyleSheet()
    names = ", ".join(b["name"] for b in my_businesses() if b["id"] in ids) or "-"
    buf = io.BytesIO(); doc = SimpleDocTemplate(buf, pagesize=A4, title="PromoPilot Report")
    def tbl(data, widths=None):
        x = Table(data, colWidths=widths, repeatRows=1)
        x.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14213D")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("GRID", (0, 0), (-1, -1), .25, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")])); return x
    el = [Paragraph("PromoPilot Marketing Report", st["Title"]), Paragraph(f"Businesses: {names}<br/>Generated: {now().replace('T', ' ')}", st["Normal"]), Spacer(1, 12),
          Paragraph("Performance", st["Heading2"]),
          tbl([["Reach", "Likes", "Comments", "Clicks", "Engagement rate"], [t["reach"], t["likes"], t["comments"], t["clicks"], f"{t['rate']}%"]]),
          Spacer(1, 10), Paragraph("Posts by status: " + ", ".join(f"{k}: {v}" for k, v in by_status.items()), st["Normal"]),
          Paragraph("AI recommendations", st["Heading2"])]
    el += [Paragraph("- " + r, st["Normal"]) for r in ai.recommend(findings(pub))]
    el += [Paragraph("Published content and ML predictions", st["Heading2"])]
    data = [["Date", "Variant", "Content", "Predicted", "Reach", "Eng."]]
    for r in pub[:40]:
        data.append([(r["published_at"] or "")[:10], r["variant"], Paragraph(r["content"][:140].encode("latin-1", "replace").decode("latin-1"), st["BodyText"]),
                     r["predicted_level"], r["reach"], r["likes"] + r["comments"] + r["clicks"]])
    el.append(tbl(data, [55, 38, 250, 55, 40, 40]) if len(data) > 1 else Paragraph("No published posts yet.", st["Normal"]))
    doc.build(el); buf.seek(0)
    return send_file(buf, mimetype="application/pdf", download_name="promopilot-report.pdf")

# ---------- admin ----------
@app.route("/admin/users")
@admin_required
def admin_users():
    return render_template("admin_users.html", items=q("SELECT u.*, (SELECT COUNT(*) FROM businesses WHERE owner_id=u.id) nb FROM users u ORDER BY u.id"))

@app.post("/admin/users/<int:uid>/<action>")
@admin_required
def admin_user_action(uid, action):
    if uid == g.user["id"]: flash("You cannot change your own account here.", "err")
    elif action == "toggle": ex("UPDATE users SET active=1-active WHERE id=?", (uid,))
    elif action == "delete":
        for b in q("SELECT id FROM businesses WHERE owner_id=?", (uid,)):
            for t in ("posts", "products", "campaigns"): ex(f"DELETE FROM {t} WHERE business_id=?", (b["id"],))
        ex("DELETE FROM businesses WHERE owner_id=?", (uid,)); ex("DELETE FROM users WHERE id=?", (uid,))
    return redirect(url_for("admin_users"))

@app.route("/admin/model")
@admin_required
def admin_model(): return render_template("admin_model.html", m=ml.METRICS)

# ---------- scheduler: publishes due posts in the background, retries on failure ----------
def simulate_metrics(level):
    lo, hi = {"High": (1200, 3000), "Medium": (500, 1400), "Low": (120, 600)}.get(level, (300, 900))
    reach = random.randint(lo, hi)
    return reach, int(reach * random.uniform(.03, .09)), int(reach * random.uniform(.004, .02)), int(reach * random.uniform(.01, .05))

def scheduler_loop():
    while True:
        time.sleep(8)
        try:
            c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
            for p in c.execute("SELECT p.*, b.fb_page FROM posts p JOIN businesses b ON b.id=p.business_id WHERE p.status='scheduled' AND p.scheduled_at<=?", (now(),)).fetchall():
                # Demo publisher: replace this block with a real Facebook Graph API call.
                if not p["fb_page"]: ok, err = False, "No Facebook Page connected to this business."
                elif random.random() < .15: ok, err = False, "Temporary Facebook API error."
                else: ok, err = True, None
                if ok:
                    r, l, cm, ck = simulate_metrics(p["predicted_level"])
                    c.execute("UPDATE posts SET status='published', published_at=?, error=NULL, reach=?, likes=?, comments=?, clicks=? WHERE id=?", (now(), r, l, cm, ck, p["id"]))
                else:
                    n = p["retries"] + 1
                    if n >= 3 or not p["fb_page"]: c.execute("UPDATE posts SET status='failed', retries=?, error=? WHERE id=?", (n, err, p["id"]))
                    else: c.execute("UPDATE posts SET retries=?, error=?, scheduled_at=? WHERE id=?", (n, err, (datetime.now() + timedelta(minutes=1)).strftime(FMT), p["id"]))
            c.commit(); c.close()
        except Exception as e:
            print("scheduler error:", e)

# ---------- setup ----------
def init_db():
    c = sqlite3.connect(DB); c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM users").fetchone():
        c.execute("INSERT INTO users(name,email,password_hash,role) VALUES('Admin','admin@promopilot.com',?, 'admin')", (generate_password_hash("admin123"),))
        c.execute("INSERT INTO users(name,email,password_hash,role) VALUES('Demo Owner','owner@promopilot.com',?, 'owner')", (generate_password_hash("owner123"),))
        c.execute("INSERT INTO businesses(owner_id,name,category,audience,tone,language,contact,fb_page) VALUES(2,'Nakshi Threads','Fashion','young women in Dhaka','friendly','English','01700000000','Nakshi Threads BD')")
        for n, d, p, cat in (("Cotton Kurti", "Soft cotton kurti with hand-block print.", 1450, "Fashion"),
                             ("Jamdani Scarf", "Lightweight scarf woven with traditional jamdani motifs.", 980, "Accessories"),
                             ("Leather Sandals", "Handmade leather sandals with cushioned sole.", 1750, "Footwear")):
            c.execute("INSERT INTO products(business_id,name,description,price,category,in_stock) VALUES(1,?,?,?,?,1)", (n, d, p, cat))
        for i in range(14):  # demo history so the dashboard is not empty
            when = datetime.now() - timedelta(days=14 - i, hours=random.randint(0, 5)); h = random.choice([10, 13, 19, 20, 21, 22])
            when = when.replace(hour=h, minute=0); lvl = "High" if h in (19, 20, 21) else random.choice(["Low", "Medium"])
            r, l, cm, ck = simulate_metrics(lvl)
            c.execute("""INSERT INTO posts(business_id,product_id,platform,kind,content,hashtags,variant,status,scheduled_at,published_at,predicted_level,source,created_at,
                         reach,likes,comments,clicks) VALUES(1,?,'Facebook',?, 'Sample post: new arrivals at Nakshi Threads!', '#NakshiThreads #ShopNow #OnlineShopBD',?, 'published',?,?,?, 'template',?,?,?,?,?)""",
                      (i % 3 + 1, random.choice(["caption", "ad"]), "AB"[i % 2], when.strftime(FMT), when.strftime(FMT), lvl, when.strftime(FMT), r, l, cm, ck))
    c.commit(); c.close()

init_db()

if __name__ == "__main__":
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print("PromoPilot running at http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
