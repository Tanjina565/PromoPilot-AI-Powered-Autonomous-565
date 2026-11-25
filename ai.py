"""Content generation (LLM with template fallback), safety check and recommendations."""
import os, re, json, random, urllib.request

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("PROMOPILOT_MODEL", "claude-sonnet-4-6")

FESTIVALS = [  # dates are approximate for religious festivals
    ("Black Friday sale", "2026-11-27"), ("12.12 mega sale", "2026-12-12"),
    ("Victory Day (Bijoy Dibosh)", "2026-12-16"), ("New Year 2027", "2027-01-01"),
    ("Valentine's Day", "2027-02-14"), ("International Mother Language Day", "2027-02-21"),
    ("Independence Day", "2027-03-26"), ("Eid-ul-Fitr (approx.)", "2027-03-10"),
    ("Pohela Boishakh", "2027-04-14"), ("Eid-ul-Adha (approx.)", "2027-05-17"),
]

BANNED = [r"100\s?%", r"guarantee[d]?", r"#\s?1\b", r"best in (the )?world", r"risk[- ]free",
          r"\b\d{1,3}\s?%\s?(off|discount)", r"\bfree (delivery|shipping|gift)\b", r"গ্যারান্টি"]

def safety_check(text):
    """Remove unsupported claims (fake discounts, false guarantees). Returns (clean, removed)."""
    removed = []
    for pat in BANNED:
        for m in re.finditer(pat, text, flags=re.I):
            removed.append(m.group(0))
        text = re.sub(pat, "", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip(), removed

def _llm(prompt):
    if not API_KEY:
        return None
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({"model": MODEL, "max_tokens": 600,
                             "messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"content-type": "application/json", "x-api-key": API_KEY,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["content"][0]["text"].strip()
    except Exception:
        return None  # LLM down -> caller uses template fallback

def _short(text, n=110):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "..."

def _tags(b, p, lang):
    words = [re.sub(r"\W+", "", w) for w in (p["name"] + " " + (p["category"] or "")).split()]
    tags = ["#" + w for w in words if len(w) > 2][:3]
    tags += ["#" + re.sub(r"\W+", "", b["name"]), "#OnlineShopBD", "#ShopNow"]
    return " ".join(dict.fromkeys(tags))

def generate(b, p, platform="Facebook", tone="friendly", goal="sale", lang="English", kind="caption", variant=0):
    """Returns dict(content, hashtags, removed, source). Built only from business + product details."""
    price = f"৳{int(p['price'])}" if p["price"] else ""
    prompt = (f"Write a {kind} for {platform} in {lang}. Tone: {tone}. Goal: {goal}. "
              f"Business: {b['name']} ({b['category']}), audience: {b['audience']}. "
              f"Product: {p['name']} - {p['description']}. Price: {price}. "
              "Use ONLY these facts. Do not invent discounts, guarantees or statistics. "
              f"Variant {'AB'[variant % 2]}: " + ("lead with a question." if variant % 2 else "lead with the benefit.")
              + " Return only the text.")
    text, source = _llm(prompt), "llm"
    if not text:
        text, source = _template(b, p, tone, goal, lang, kind, variant, price), "template"
    text, removed = safety_check(text)
    return {"content": text, "hashtags": _tags(b, p, lang), "removed": removed, "source": source}

def _template(b, p, tone, goal, lang, kind, v, price):
    d = _short(p["description"])
    emoji = {"friendly": "😊", "playful": "🎉", "professional": ""}.get(tone, "✨")
    cta_en = {"sale": "Order today.", "launch": "Just launched - be the first to try it.",
              "festival": "Make your celebration special.", "awareness": "Learn more in our page."}.get(goal, "Order today.")
    cta_bn = {"sale": "আজই অর্ডার করুন।", "launch": "নতুন এসেছে - সবার আগে নিন।",
              "festival": "উৎসবকে করুন আরও স্পেশাল।", "awareness": "বিস্তারিত জানতে মেসেজ করুন।"}.get(goal, "আজই অর্ডার করুন।")
    bn = lang.lower().startswith("b")
    if kind == "description":
        return (f"{p['name']} - {d} {b['name']} থেকে এখন পাচ্ছেন {price} এ।" if bn
                else f"{p['name']}: {d} Made for {b['audience'] or 'everyone'}. Available at {b['name']} for {price}.")
    if kind == "ad":
        return (f"{p['name']} | {d} | {price} | {cta_bn}" if bn else f"{p['name']} - {d} Only {price}. {cta_en}")
    if kind in ("sms", "email"):
        return (f"{b['name']}: {p['name']} এখন {price}। {cta_bn}" if bn
                else f"{b['name']}: {p['name']} is now {price}. {cta_en}")
    if bn:
        return (f"{emoji} {p['name']} কি আপনার পছন্দ? {d} মাত্র {price}। {cta_bn}" if v % 2
                else f"{emoji} {p['name']} এখন {b['name']} এ! {d} মূল্য {price}। {cta_bn}")
    return (f"{emoji} Looking for {p['name']}? {d} Just {price} at {b['name']}. {cta_en}" if v % 2
            else f"{emoji} Meet {p['name']}! {d} Now {price} at {b['name']}. {cta_en}")

def idea_for(goal, i):
    ideas = {"sale": ["Announce the sale", "Product spotlight", "Customer-favourite pick", "Last-chance reminder"],
             "festival": ["Festival greeting", "Gift idea", "Behind the scenes", "Festival wrap-up"],
             "launch": ["Teaser", "Launch day", "Feature highlight", "How to order"]}.get(goal, ["Product spotlight", "Benefit highlight"])
    return ideas[i % len(ideas)]

def recommend(stats):
    """stats: list of plain-text findings from the data. LLM rewrites them if available."""
    text = _llm("Turn these marketing findings into 3 short, practical recommendations (plain text, one per line):\n"
                + "\n".join(stats)) if stats else None
    if text:
        return [l.lstrip("-• ").strip() for l in text.splitlines() if l.strip()][:5]
    return stats or ["Publish a few posts first - recommendations appear once there is performance data."]
