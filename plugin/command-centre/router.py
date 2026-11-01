"""Request classification: direct Claude (Route A) vs Astra planning (Route B).

Deterministic scoring first; defaults toward Claude. Planning only when the request
shows real planning value: broad scope, product/creative/architecture decisions,
several deliverables, migrations, cross-system work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CLAUDE = "claude"
ASTRA = "astra"


@dataclass
class Decision:
    route: str
    reason: str
    score: int
    forced: bool
    text: str  # request text with any override prefix removed


# (pattern, weight, label). Weights are summed; label of the strongest hit becomes the reason.
_PLAN_SIGNALS: list[tuple[str, int, str]] = [
    (r"\b(re-?design|overhaul|revamp|rethink|re-?imagine|re-?architect|rebuild)\b", 3, "redesign"),
    (r"\bfrom scratch\b", 3, "greenfield build"),
    (r"\bdesign and (build|implement|ship)\b", 3, "design + build"),
    (r"\b(new|whole new|brand[- ]new)\s+(app|application|product|platform|saas|service|website|site|game|feature set|system)\b", 3, "new product"),
    (r"\b(build|create|make|develop)\s+(me\s+)?(an?\s+)?(new\s+)?(app|application|platform|saas|product|game|website|site)\b", 2, "new product"),
    (r"\b(major|big|large|new)\s+feature\b", 2, "major feature"),
    (r"\b(marketing|ad|advertising|social)\s+campaign\b|\bcampaign\b", 3, "campaign / creative direction"),
    (r"\blaunch (strategy|plan)\b|\bgo[- ]to[- ]market\b|\bstrategy\b|\broadmap\b", 3, "strategy"),
    (r"\b(visual identity|brand identity|rebrand|branding|brand system|design system)\b", 3, "brand / identity"),
    (r"\b(advert|advertisement|commercial|promo video|brand film|trailer)\b", 3, "ad / film concept"),
    (r"\bonboarding (flow|experience|journey)\b|\buser journey\b|\b(ux|user experience) (flow|overhaul)\b", 2, "product/UX flow"),
    (r"\bmigrat(e|ion)\b|\bport (it|this|the \w+) to\b|\bupgrade (the )?(whole|entire)\b", 3, "migration"),
    (r"\b(architecture|architect)\b|\bsystem design\b", 3, "architecture"),
    (r"\bintegrat(e|ion) (with|between)\b|\bacross (systems|services|repos)\b|\bmultiple (systems|services|repos)\b", 2, "cross-system"),
    (r"\b(complex|end[- ]to[- ]end|multi[- ]step|full[- ]stack)\b", 1, "complexity"),
    (r"\b(entire|whole|complete|comprehensive|overall|every (page|screen))\b", 1, "broad scope"),
    (r"\b(concepts?|creative direction|ideas for|brainstorm|explore options|moodboard)\b", 2, "creative interpretation"),
    (r"\brefactor (the )?(whole|entire|all)\b|\blarge refactor\b", 3, "large refactor"),
    (r"\b(landing page|website|homepage)\b.*\b(motion|cinematic|animated|video)\b", 2, "motion-heavy creative build"),
]

_DIRECT_SIGNALS: list[tuple[str, int]] = [
    (r"^\s*(please\s+)?(change|make|set|fix|rename|move|remove|delete|add|update|bump|tweak|adjust|swap|replace|hide|show|align|center|centre|increase|decrease|shorten|lengthen|revert|undo)\b", 2),
    (r"\b\d+\s?(px|rem|em|%|ms|s)\b|#[0-9a-fA-F]{3,8}\b", 2),
    (r"\b(run|re-?run) (the )?(tests?|lint|build|typecheck)\b|\bcommit\b|\bpush\b|\bdeploy\b", 3),
    (r"\b(typo|error|exception|traceback|stack trace|failing test|bug|broken|crash(es|ing)?)\b", 2),
    (r"[\w./-]+\.(ts|tsx|js|jsx|py|rs|go|css|scss|html|json|md|yml|yaml|swift|kt)\b|:\d+\b", 1),
    (r"\b(button|padding|margin|colou?r|font|copy|text|modal|navbar|icon|label|spacing|border|shadow|animation)\b", 1),
]


def _strip_override(text: str, overrides: dict) -> tuple[str | None, str]:
    stripped = text.lstrip()
    low = stripped.lower()
    for route in (CLAUDE, ASTRA):
        for prefix in overrides.get(route, []):
            if low.startswith(prefix.lower()):
                rest = stripped[len(prefix):]
                # "@claude" must be a whole token, not "@claudeX"
                if rest and not (rest[0].isspace() or prefix.endswith(":")):
                    continue
                return route, rest.strip()
    return None, text.strip()


def classify(text: str, cfg: dict) -> Decision:
    rcfg = cfg.get("router", {}) or {}
    overrides = rcfg.get("overrides") or {
        CLAUDE: ["@claude", "claude:"],
        ASTRA: ["@astra", "astra:", "@plan", "plan:"],
    }
    forced, body = _strip_override(text, overrides)
    if forced == CLAUDE:
        return Decision(CLAUDE, "manual override (@claude)", 0, True, body)
    if forced == ASTRA:
        return Decision(ASTRA, "manual override (@astra)", 0, True, body)

    low = body.lower()
    plan_score, best_label, best_weight = 0, "", 0
    for pattern, weight, label in _PLAN_SIGNALS:
        if re.search(pattern, low):
            plan_score += weight
            if weight > best_weight:
                best_label, best_weight = label, weight
    direct_score = sum(w for p, w in _DIRECT_SIGNALS if re.search(p, low))

    # Several distinct deliverables ("a logo, a site and a launch video") suggest planning.
    deliverables = len(re.findall(r"\b(and|plus|also|as well as)\b", low))
    if deliverables >= 3 and len(low) > 160:
        plan_score += 1
        best_label = best_label or "several deliverables"

    # Small-scope signals pull toward Claude. A strong planning hit (weight 3: redesign, campaign,
    # migration…) is only outweighed by very specific direct signals (typo, px values, file paths…).
    penalty = direct_score if best_weight < 3 else max(0, direct_score - 3)
    score = plan_score - penalty
    threshold = int(rcfg.get("plan_threshold", 3))
    if score >= threshold:
        return Decision(ASTRA, f"{best_label} — planning adds value", score, False, body)
    return Decision(CLAUDE, "scoped implementation", score, False, body)


_TASK_VERBS = re.compile(
    r"\b(create|build|make|write|fix|change|add|edit|implement|generate|refactor|run|commit|deploy|"
    r"install|delete|remove|rename|update|set up|setup|render|record|transcribe|dub|animate)\b", re.I)
_QUESTION = re.compile(
    r"^\s*(what|why|how|who|when|where|which|is|are|do|does|did|can|could|would|should|will|"
    r"explain|tell me|describe|compare|summari[sz]e|help me (understand|think|decide)|thoughts on|"
    r"hi|hey|hello|thanks|thank you)\b", re.I)


def looks_like_chat(text: str) -> bool:
    """Generic conversation/question with nothing to execute (used only when no project is targeted)."""
    t = text.strip()
    if re.match(r"^\s*(can|could|would|will)\s+(you|u)\s+(please\s+)?" + _TASK_VERBS.pattern, t, re.I):
        return False  # "can you fix the navbar" is a task, not a question
    if _TASK_VERBS.search(t) and not _QUESTION.match(t):
        return False
    return bool(_QUESTION.match(t) or t.endswith("?") or len(t.split()) <= 3)


# --- approval-state reply interpretation ------------------------------------------------------

_APPROVE = re.compile(
    r"^\s*(yes|yep|yeah|yup|y|approved?|go|go ahead|go for it|do it|looks good|lgtm|ship it|"
    r"proceed|sounds good|ok|okay|perfect|great|confirmed?|let'?s go|build it|execute)\b[\s!.,]*",
    re.I,
)
_CANCEL = re.compile(r"^\s*(cancel|stop|abort|never ?mind|scrap (it|this)|drop it|no thanks)\b", re.I)


def interpret_reply(text: str) -> tuple[str, str]:
    """Return (kind, modification). kind: approve | approve_with_changes | cancel | revise."""
    if _CANCEL.match(text):
        return "cancel", ""
    m = _APPROVE.match(text)
    if m:
        rest = text[m.end():].strip()
        rest = re.sub(r"^(but|and|except|just|though|however)\b[\s,:-]*", "", rest, flags=re.I).strip()
        if len(rest) >= 3:
            return "approve_with_changes", rest
        return "approve", ""
    return "revise", text.strip()
