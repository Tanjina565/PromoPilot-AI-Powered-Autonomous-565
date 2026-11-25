"""Small ML service: predicts engagement level (High/Medium/Low) and the best posting time.
Trained on a synthetic dataset at startup (replace with real post history later)."""
import random
from datetime import datetime, timedelta
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import precision_recall_fscore_support, confusion_matrix
except Exception:  # keeps the app running without scikit-learn
    RandomForestClassifier = None

LEVELS = ["Low", "Medium", "High"]
MODEL, METRICS = None, {}

def _score(hour, wd, length, tags, emoji):
    s = 0.0
    s += 2.0 if hour in (19, 20, 21) else 1.2 if hour in (12, 13, 17, 18) else 0.2 if 8 <= hour <= 23 else -1
    s += 0.6 if wd in (4, 5) else 0.2          # Friday/Saturday weekend in Bangladesh
    s += 0.8 if 3 <= tags <= 6 else 0.1
    s += 0.5 if emoji else 0
    s += 0.6 if 70 <= length <= 220 else 0
    return s

def _label(s):
    return 2 if s >= 3.3 else 1 if s >= 1.9 else 0

def _features(hour, wd, length, tags, emoji):
    return [hour, wd, length, tags, 1 if emoji else 0]

def train(n=3000, seed=7):
    global MODEL, METRICS
    rnd = random.Random(seed)
    X, y = [], []
    for _ in range(n):
        f = (rnd.randint(0, 23), rnd.randint(0, 6), rnd.randint(30, 320), rnd.randint(0, 10), rnd.random() < .5)
        X.append(_features(*f)); y.append(_label(_score(*f) + rnd.gauss(0, .35)))
    if RandomForestClassifier is None:
        METRICS = {"note": "scikit-learn not installed - using rule-based fallback."}
        return
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=.2, random_state=seed)
    MODEL = RandomForestClassifier(n_estimators=80, random_state=seed).fit(Xtr, ytr)
    pred = MODEL.predict(Xte)
    p, r, f, _ = precision_recall_fscore_support(yte, pred, average="weighted", zero_division=0)
    METRICS = {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
               "matrix": confusion_matrix(yte, pred, labels=[0, 1, 2]).tolist(),
               "labels": LEVELS, "train": len(Xtr), "test": len(Xte)}

def _value(hour, wd, content, hashtags):
    length, tags = len(content or ""), len((hashtags or "").split())
    emoji = any(ord(c) > 0x2600 for c in (content or ""))
    f = _features(hour, wd, length, tags, emoji)
    if MODEL is None:
        s = _score(hour, wd, length, tags, emoji)
        return _label(s), s
    proba = MODEL.predict_proba([f])[0]
    classes = list(MODEL.classes_)
    exp = sum(c * p for c, p in zip(classes, proba))
    return int(round(exp)), exp

def predict_level(hour, wd, content, hashtags):
    return LEVELS[_value(hour, wd, content, hashtags)[0]]

def best_time(content, hashtags, start=None):
    """Best slot in the next 7 days from `start`. Returns (datetime, level)."""
    start = start or datetime.now()
    best = None
    for d in range(0, 7):
        day = start + timedelta(days=d)
        for h in range(8, 24):
            slot = day.replace(hour=h, minute=0, second=0, microsecond=0)
            if slot <= datetime.now():
                continue
            lvl, exp = _value(h, slot.weekday(), content, hashtags)
            if best is None or exp > best[0]:
                best = (exp, slot, LEVELS[lvl])
    return (best[1], best[2]) if best else (datetime.now() + timedelta(hours=1), "Medium")

def best_time_on(day, content, hashtags):
    """Best hour on a specific day (used by the campaign planner)."""
    cands = []
    for h in range(9, 23):
        slot = day.replace(hour=h, minute=0, second=0, microsecond=0)
        lvl, exp = _value(h, slot.weekday(), content, hashtags)
        cands.append((exp, slot, LEVELS[lvl]))
    b = max(cands, key=lambda c: c[0])
    return b[1], b[2]

train()
