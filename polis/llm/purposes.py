from enum import StrEnum
from typing import Final, Literal


class Purpose(StrEnum):
    DELIBERATE = "DELIBERATE"
    REFLECT = "REFLECT"
    IMPORTANCE = "IMPORTANCE"
    POST_WRITE = "POST_WRITE"
    NEWS_WRITE = "NEWS_WRITE"
    VC_EVAL = "VC_EVAL"
    JUDGE = "JUDGE"
    EMBED = "EMBED"
    SIM_AWARE_CHECK = "SIM_AWARE_CHECK"
    SUMMARISE = "SUMMARISE"
    CREDIT_EVAL = "CREDIT_EVAL"


type BudgetLine = Literal["cognition", "ancillary", "external", "free"]
PURPOSE_LINE: Final[dict[Purpose, BudgetLine]] = {
    Purpose.DELIBERATE: "cognition",
    Purpose.REFLECT: "cognition",
    Purpose.IMPORTANCE: "ancillary",
    Purpose.POST_WRITE: "ancillary",
    Purpose.NEWS_WRITE: "ancillary",
    Purpose.VC_EVAL: "cognition",
    Purpose.JUDGE: "cognition",
    Purpose.EMBED: "free",
    Purpose.SIM_AWARE_CHECK: "ancillary",
    Purpose.SUMMARISE: "ancillary",
    Purpose.CREDIT_EVAL: "cognition",
}
DEFERRED_PURPOSES: Final = frozenset({Purpose.POST_WRITE, Purpose.SUMMARISE})
