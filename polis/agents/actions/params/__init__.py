from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from polis.agents.actions.params.banking import (
    ApplyForLoanParams,
    DefaultParams,
    DepositParams,
    OpenAccountParams,
    RepayLoanParams,
    WithdrawParams,
)
from polis.agents.actions.params.base import ActionParams
from polis.agents.actions.params.education import (
    DropOutParams,
    EnrolParams,
    StudyParams,
    TakeExamParams,
)
from polis.agents.actions.params.exchange import (
    CancelOrderParams,
    IpoListParams,
    ShortParams,
    SubmitOrderParams,
)
from polis.agents.actions.params.goods import (
    BuyGoodParams,
    ProduceParams,
    RestockParams,
    SetPriceParams,
)
from polis.agents.actions.params.labour import (
    AcceptOfferParams,
    ApplyForJobParams,
    DeclineOfferParams,
    FireEmployeeParams,
    MakeOfferParams,
    NegotiateWageParams,
    PostVacancyParams,
    QuitJobParams,
    WorkParams,
)
from polis.agents.actions.params.law import (
    CommitCrimeParams,
    FileSuitParams,
    ReportCrimeParams,
    RetainCounselParams,
    RuleParams,
    SettleParams,
    TestifyParams,
)
from polis.agents.actions.params.media import (
    CommentParams,
    FollowParams,
    LikeParams,
    PostParams,
    PublishArticleParams,
    RepostParams,
    RetractParams,
    UnfollowParams,
)
from polis.agents.actions.params.meta import NullActionParams
from polis.agents.actions.params.polity import (
    AnnounceCandidacyParams,
    CampaignParams,
    FoundPartyParams,
    JoinPartyParams,
    LobbyParams,
    ProposePolicyParams,
    VoteParams,
)
from polis.agents.actions.params.social import (
    BefriendParams,
    CourtParams,
    DissolveUnionParams,
    HaveChildIntentParams,
    ProposeUnionParams,
)
from polis.agents.actions.params.speech import (
    BroadcastParams,
    DirectMessageParams,
    SayParams,
)
from polis.agents.actions.params.ventures import (
    AcquireParams,
    DeclareDividendParams,
    FileBankruptcyParams,
    FoundCompanyParams,
    InvestParams,
    IssueTermSheetParams,
    PitchParams,
    SellStakeParams,
)
from polis.agents.actions.params.world import (
    EatParams,
    IdleParams,
    MoveToParams,
    RentHomeParams,
    SleepParams,
)
from polis.agents.actions.types import ActionType

PARAMS_MODELS: Final[Mapping[ActionType, type[ActionParams]]] = MappingProxyType(
    {
        ActionType.MOVE_TO: MoveToParams,
        ActionType.IDLE: IdleParams,
        ActionType.SLEEP: SleepParams,
        ActionType.EAT: EatParams,
        ActionType.RENT_HOME: RentHomeParams,
        ActionType.SAY: SayParams,
        ActionType.DIRECT_MESSAGE: DirectMessageParams,
        ActionType.BROADCAST: BroadcastParams,
        ActionType.APPLY_FOR_JOB: ApplyForJobParams,
        ActionType.ACCEPT_OFFER: AcceptOfferParams,
        ActionType.DECLINE_OFFER: DeclineOfferParams,
        ActionType.QUIT_JOB: QuitJobParams,
        ActionType.NEGOTIATE_WAGE: NegotiateWageParams,
        ActionType.POST_VACANCY: PostVacancyParams,
        ActionType.MAKE_OFFER: MakeOfferParams,
        ActionType.FIRE_EMPLOYEE: FireEmployeeParams,
        ActionType.WORK: WorkParams,
        ActionType.ENROL: EnrolParams,
        ActionType.STUDY: StudyParams,
        ActionType.DROP_OUT: DropOutParams,
        ActionType.TAKE_EXAM: TakeExamParams,
        ActionType.BUY_GOOD: BuyGoodParams,
        ActionType.SET_PRICE: SetPriceParams,
        ActionType.PRODUCE: ProduceParams,
        ActionType.RESTOCK: RestockParams,
        ActionType.SUBMIT_ORDER: SubmitOrderParams,
        ActionType.CANCEL_ORDER: CancelOrderParams,
        ActionType.SHORT: ShortParams,
        ActionType.IPO_LIST: IpoListParams,
        ActionType.OPEN_ACCOUNT: OpenAccountParams,
        ActionType.DEPOSIT: DepositParams,
        ActionType.WITHDRAW: WithdrawParams,
        ActionType.APPLY_FOR_LOAN: ApplyForLoanParams,
        ActionType.REPAY_LOAN: RepayLoanParams,
        ActionType.DEFAULT: DefaultParams,
        ActionType.FOUND_COMPANY: FoundCompanyParams,
        ActionType.PITCH: PitchParams,
        ActionType.ISSUE_TERM_SHEET: IssueTermSheetParams,
        ActionType.INVEST: InvestParams,
        ActionType.ACQUIRE: AcquireParams,
        ActionType.SELL_STAKE: SellStakeParams,
        ActionType.FILE_BANKRUPTCY: FileBankruptcyParams,
        ActionType.DECLARE_DIVIDEND: DeclareDividendParams,
        ActionType.POST: PostParams,
        ActionType.REPOST: RepostParams,
        ActionType.LIKE: LikeParams,
        ActionType.COMMENT: CommentParams,
        ActionType.FOLLOW: FollowParams,
        ActionType.UNFOLLOW: UnfollowParams,
        ActionType.PUBLISH_ARTICLE: PublishArticleParams,
        ActionType.RETRACT: RetractParams,
        ActionType.JOIN_PARTY: JoinPartyParams,
        ActionType.ANNOUNCE_CANDIDACY: AnnounceCandidacyParams,
        ActionType.CAMPAIGN: CampaignParams,
        ActionType.VOTE: VoteParams,
        ActionType.PROPOSE_POLICY: ProposePolicyParams,
        ActionType.LOBBY: LobbyParams,
        ActionType.FOUND_PARTY: FoundPartyParams,
        ActionType.COMMIT_CRIME: CommitCrimeParams,
        ActionType.REPORT_CRIME: ReportCrimeParams,
        ActionType.FILE_SUIT: FileSuitParams,
        ActionType.RETAIN_COUNSEL: RetainCounselParams,
        ActionType.TESTIFY: TestifyParams,
        ActionType.SETTLE: SettleParams,
        ActionType.RULE: RuleParams,
        ActionType.BEFRIEND: BefriendParams,
        ActionType.COURT: CourtParams,
        ActionType.PROPOSE_UNION: ProposeUnionParams,
        ActionType.DISSOLVE_UNION: DissolveUnionParams,
        ActionType.HAVE_CHILD_INTENT: HaveChildIntentParams,
        ActionType.NULL_ACTION: NullActionParams,
    }
)

assert len(ActionType) == 71, "the C10 action protocol is closed at 71 types"
assert set(PARAMS_MODELS) == set(ActionType), "every action type needs a params model"

__all__ = [
    "PARAMS_MODELS",
    "ActionParams",
]
