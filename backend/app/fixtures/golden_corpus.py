"""The reference negotiation as a corpus of sanitized .eml messages.

These are already-sanitized reconstructions: the pricing, wording, chronology and
thread structure of the real Civic negotiation, with every personal detail replaced by
a fixture value. They are safe to commit, and they are what ``tests/fixtures/golden``
contains.

The real correspondence never enters the repository. ``app.ingestion.sanitize`` exists
to turn it into this same shape locally, and the corpus directory is gitignored.

Written as .eml rather than as Python objects on purpose — the tests then exercise
header parsing, threading and MIME handling on the way in, which is where ingestion
bugs actually live.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.ingestion import eml
from app.ingestion.messages import RawMessage

BUYER = "buyer@example.test"
BUYER_NAME = "Test Buyer"

BASE = datetime(2026, 8, 30, 9, 0, 0)

SUBJECT = "2026 Civic Hatchback Sport — itemized out-the-door request"


@dataclass(frozen=True)
class DealerSeed:
    name: str
    domain: str
    city: str
    state: str
    distance_miles: float
    is_local: bool = False


DEALERS: tuple[DealerSeed, ...] = (
    DealerSeed("Honda of Westport", "hondaofwestport.example.test", "Westport", "CT", 62),
    DealerSeed("Honda of Stamford", "hondaofstamford.example.test", "Stamford", "CT", 41, True),
    DealerSeed("Curry Honda", "curryhonda.example.test", "Scarsdale", "NY", 78),
    DealerSeed("Ocean Honda Milford", "oceanhondamilford.example.test", "Milford", "CT", 88),
    DealerSeed("White Plains Honda", "whiteplainshonda.example.test", "White Plains", "NY", 71),
    DealerSeed("Mount Kisco Honda", "mountkiscohonda.example.test", "Mount Kisco", "NY", 84),
    DealerSeed("Tarrytown Honda", "tarrytownhonda.example.test", "Tarrytown", "NY", 69),
)

INQUIRY = """Hello,

I'm shopping for a new 2026 Honda Civic Hatchback Sport (non-hybrid), in a dark or
subdued colour — no white. I'm paying cash and registering in Pennsylvania (ZIP 18435),
with no trade-in.

I'd like an itemized out-the-door price in writing for a specific VIN: selling price,
every dealer fee, any add-ons, plus tax and registration as separate lines. I don't
want any dealer add-ons or maintenance packages.

I'm contacting several dealers and will buy from whoever sends the best written number.
Email is best for me.

Thanks."""


def _at(days: float, hour: int, minute: int) -> datetime:
    return (BASE + timedelta(days=days)).replace(hour=hour, minute=minute, second=0)


def _message(
    *,
    key: str,
    when: datetime,
    subject: str,
    body: str,
    from_address: str,
    from_name: str,
    to_address: str,
    thread: str,
    in_reply_to: str | None = None,
) -> RawMessage:
    return RawMessage(
        source_system="golden",
        source_identifier=f"<{key}@dealbench.test>",
        sent_at=when,
        thread_identifier=thread,
        rfc822_message_id=f"<{key}@dealbench.test>",
        in_reply_to=in_reply_to,
        references=(thread,) if in_reply_to else (),
        from_address=from_address,
        from_name=from_name,
        to_addresses=(to_address,),
        subject=subject,
        body_text=body,
    )


def build() -> list[RawMessage]:
    """The corpus, in the order it happened."""
    messages: list[RawMessage] = []
    threads = {d.name: f"<thread-{d.domain.split('.')[0]}@dealbench.test>" for d in DEALERS}

    def inquiry_to(dealer: DealerSeed, contact: str, minute: int) -> RawMessage:
        key = f"t0-inquiry-{dealer.domain.split('.')[0]}"
        return _message(
            key=key,
            when=_at(0, 9, minute),
            subject=SUBJECT,
            body=INQUIRY,
            from_address=BUYER,
            from_name=BUYER_NAME,
            to_address=contact,
            thread=threads[dealer.name],
        )

    # ---- T0: the same inquiry to all seven stores -------------------------
    first_contacts = {
        "Honda of Westport": "chris.benton@hondaofwestport.example.test",
        "Honda of Stamford": "edwin.sanchez@hondaofstamford.example.test",
        "Curry Honda": "jessica.rivera@curryhonda.example.test",
        "Ocean Honda Milford": "patrick.mackin@oceanhondamilford.example.test",
        "White Plains Honda": "dana.whitfield@whiteplainshonda.example.test",
        "Mount Kisco Honda": "amber.chase@mountkiscohonda.example.test",
        "Tarrytown Honda": "ebony.bryant@tarrytownhonda.example.test",
    }
    for index, dealer in enumerate(DEALERS):
        messages.append(inquiry_to(dealer, first_contacts[dealer.name], 10 + index))

    def reply(
        key: str,
        dealer: str,
        when: datetime,
        sender: str,
        name: str,
        body: str,
        subject: str | None = None,
    ) -> RawMessage:
        return _message(
            key=key,
            when=when,
            subject=subject or f"Re: {SUBJECT}",
            body=body,
            from_address=sender,
            from_name=name,
            to_address=BUYER,
            thread=threads[dealer],
            in_reply_to=threads[dealer],
        )

    def outbound(key: str, dealer: str, when: datetime, to: str, body: str,
                 subject: str | None = None) -> RawMessage:
        return _message(
            key=key,
            when=when,
            subject=subject or f"Re: {SUBJECT}",
            body=body,
            from_address=BUYER,
            from_name=BUYER_NAME,
            to_address=to,
            thread=threads[dealer],
            in_reply_to=threads[dealer],
        )

    # ---- Mount Kisco: client services hands off, promises pricing today ----
    messages.append(
        reply(
            "t1-kisco-amber",
            "Mount Kisco Honda",
            _at(0, 15, 40),
            "amber.chase@mountkiscohonda.example.test",
            "Amber Chase",
            """Hi, thanks for contacting Mount Kisco Honda. I've passed this to our sales
manager James, who will put together pricing for you today.

Amber Chase
Client Services""",
        )
    )

    # ---- Tarrytown: a visit is the price of a price ------------------------
    messages.append(
        reply(
            "t2-tarrytown-ebony",
            "Tarrytown Honda",
            _at(0, 18, 2),
            "ebony.bryant@tarrytownhonda.example.test",
            "Ebony Bryant",
            """Thank you for contacting Tarrytown Honda.

To guarantee you our absolute best price we'd need you to come in and see the vehicle
in person. Our manager can then put together his best offer for you.

Ebony Bryant
Client Services""",
        )
    )

    # ---- White Plains: no numbers, just a phone number request -------------
    messages.append(
        reply(
            "t3-whiteplains-dana",
            "White Plains Honda",
            _at(1, 8, 15),
            "dana.whitfield@whiteplainshonda.example.test",
            "Dana Whitfield",
            """Thank you for your interest in White Plains Honda! We do have Civic Sport
Hatchbacks available.

What's the best number to reach you at, and what's a good time? Give me a call and I
can go over everything with you.

Dana""",
        )
    )

    # ---- Tarrytown again, this time from the lead system -------------------
    # Signed by a sales manager, sent from the dealership's own domain, and composed
    # entirely of the buyer's own inquiry played back. Only the restatement check can
    # catch this one, which is the point of including it.
    automated_body = """I see you were looking for a new 2026 Honda Civic Hatchback Sport
(non-hybrid), in a dark or subdued colour — no white, paying cash and registering in
Pennsylvania with no trade-in, and that you'd like an itemized out-the-door price in
writing for a specific VIN: selling price, every dealer fee, any add-ons, plus tax and
registration as separate lines, with no dealer add-ons or maintenance packages.

I'd love to help! When can you come in?

Corey Smith
Sales Manager"""
    messages.append(
        reply(
            "t4-tarrytown-corey-1",
            "Tarrytown Honda",
            _at(1, 9, 1),
            "corey.smith@tarrytownhonda.example.test",
            "Corey Smith",
            automated_body,
            subject="Following up on your 2026 Honda Civic Hatchback Sport inquiry",
        )
    )

    # ---- Westport: the clean written benchmark -----------------------------
    messages.append(
        reply(
            "t5-westport-quote",
            "Honda of Westport",
            _at(1, 11, 24),
            "chris.benton@hondaofwestport.example.test",
            "Chris Benton",
            """Good morning,

Happy to put this in writing. We have a 2026 Civic Sport Hatchback in Urban Gray with a
black interior, 16 miles on it. It is a new car — not a demo, not a loaner, no damage
reported.

Selling price       $28,035.00
Dealer fee          $699.00
Registration        $253.00
PA sales tax        $1,688.10
------------------------------
Out the door        $30,775.10

No add-ons on this one and no financing requirement — that's the cash price. Let me
know if you'd like me to hold it.

Chris Benton
Internet Sales""",
        )
    )

    # ---- Curry: a good number with strings attached -------------------------
    messages.append(
        reply(
            "t6-curry-jessica",
            "Curry Honda",
            _at(1, 13, 2),
            "jessica.rivera@curryhonda.example.test",
            "Jessica Rivera",
            """Hi! Great news, we have Sport Hatchbacks in stock.

Our advertised price is $27,954 which includes our $175 doc fee. That price does
require financing through Honda Financial — 7.14% for 84 months.

For the final out-the-door number my manager John has to sign off, so give me a call or
let me know the best time to reach you and I'll have him walk you through it.

Jessica Rivera""",
        )
    )

    # ---- Stamford: opening offer, with add-ons ------------------------------
    messages.append(
        reply(
            "t7-stamford-quote-1",
            "Honda of Stamford",
            _at(1, 16, 3),
            "edwin.sanchez@hondaofstamford.example.test",
            "Edwin Sanchez",
            """As promised:

MSRP                $29,090.00
Selling price       $28,090.00
VIN etching         $269.00
Wheel locks         $129.00
Doc fee             $699.00
Registration        $368.00
Sales tax           $1,751.22
--------------------------------
Out the door        $31,306.22

Meteorite Gray, VIN 19XFL2H81TE040705, 10 miles. Brand new, not a demo.

Edwin Sanchez
Sales Manager""",
            subject="Your Civic Sport Hatchback — numbers",
        )
    )

    messages.append(
        outbound(
            "t8-buyer-to-curry",
            "Curry Honda",
            _at(1, 17, 30),
            "jessica.rivera@curryhonda.example.test",
            """Thanks Jessica. I'd rather not do this by phone — could you email the full
itemized out-the-door figure?

Also, on the financing requirement: is there a prepayment penalty, a minimum number of
payments, a minimum amount financed, or a discount clawback if the loan is paid off
early?""",
        )
    )

    # ---- Ocean: a discount, and an ask to come in ---------------------------
    messages.append(
        reply(
            "t9-ocean-patrick",
            "Ocean Honda Milford",
            _at(2, 9, 48),
            "patrick.mackin@oceanhondamilford.example.test",
            "Patrick Mackin",
            """Thanks for reaching out. MSRP on the Sport Hatchback is $29,320.

I went to my manager and got approval for $500 off, so $28,820 selling price. If we get
close on numbers, would you be able to come in so we can finalise? We do better when
people come down.

Patrick Mackin""",
        )
    )

    # ---- The leverage message ------------------------------------------------
    messages.append(
        outbound(
            "t10-buyer-to-stamford",
            "Honda of Stamford",
            _at(2, 10, 5),
            "edwin.sanchez@hondaofstamford.example.test",
            """Edwin,

Thanks. Two things.

The quote has VIN etching at $269 and wheel locks at $129 — $398 I didn't ask for and
don't want.

Separately, I have a written offer from another Honda store at $30,775.10 out the door
on the same car, no add-ons. I'd rather buy locally from you. If you can beat that
number I'll commit today.""",
            subject="Re: Your Civic Sport Hatchback — numbers",
        )
    )

    messages.append(
        reply(
            "t11-kisco-james",
            "Mount Kisco Honda",
            _at(2, 11, 5),
            "james.woods@mountkiscohonda.example.test",
            "James Woods",
            """Good morning, James here, sales manager at Mount Kisco Honda.

I'd love to earn your business. Rather than go back and forth over email, what's the
best time to reach you today? Give me a call and I'll make sure we take care of you.

James Woods""",
            subject="Civic Sport Hatchback",
        )
    )

    messages.append(
        outbound(
            "t12-buyer-to-ocean",
            "Ocean Honda Milford",
            _at(2, 16, 20),
            "patrick.mackin@oceanhondamilford.example.test",
            """Thanks Patrick. $28,820 isn't there yet — I'm working from written offers
around $28,000 selling.

I'll do $27,750 with no add-ons, and I can close by email today. Could you send the
itemized out-the-door figure at that price?""",
        )
    )

    # ---- The same Tarrytown template again, 48 hours later ------------------
    messages.append(
        reply(
            "t13-tarrytown-corey-2",
            "Tarrytown Honda",
            _at(3, 9, 1),
            "corey.smith@tarrytownhonda.example.test",
            "Corey Smith",
            automated_body,
            subject="Following up on your 2026 Honda Civic Hatchback Sport inquiry",
        )
    )

    # ---- Stamford beats the benchmark ---------------------------------------
    messages.append(
        reply(
            "t14-stamford-quote-2",
            "Honda of Stamford",
            _at(3, 15, 40),
            "edwin.sanchez@hondaofstamford.example.test",
            "Edwin Sanchez",
            """I took this to my GM. Here's where we can land:

MSRP                    $29,090.00
Adjusted selling price  $27,329.42
VIN etching             $269.00
Wheel locks             $129.00
Doc fee                 $699.00
Sales tax               $1,705.59
Non-tax fees            $368.00
----------------------------------
Out the door            $30,500.01

That beats the offer you sent me and you're buying local. The etch and locks are
already on the car so I can't strip them, but I've taken the money out of the selling
price to cover them.

Edwin""",
            subject="Re: Your Civic Sport Hatchback — revised",
        )
    )

    # ---- Noise: a broadcast that belongs to no dealership --------------------
    # Every real mailbox has one. It must land in the review queue rather than being
    # attached to whichever dealer happens to be nearest.
    messages.append(
        _message(
            key="t15-unrelated-blast",
            when=_at(3, 18, 30),
            subject="Your new car search",
            body="""Still looking? Thousands of vehicles are waiting for you.

Click here to browse inventory near you.""",
            from_address="noreply@autoleadsnetwork.example.test",
            from_name="Auto Leads Network",
            to_address=BUYER,
            thread="<thread-blast@dealbench.test>",
        )
    )

    messages.append(
        outbound(
            "t16-buyer-accepts",
            "Honda of Stamford",
            _at(4, 9, 30),
            "edwin.sanchez@hondaofstamford.example.test",
            """Edwin — that works. $30,500.01 out the door on VIN 19XFL2H81TE040705,
Meteorite Gray. Let's do it. Send me the buyer's order and let me know when I can pick
it up.""",
            subject="Re: Your Civic Sport Hatchback — revised",
        )
    )

    messages.append(
        outbound(
            "t17-buyer-closes-tarrytown",
            "Tarrytown Honda",
            _at(4, 10, 0),
            "ebony.bryant@tarrytownhonda.example.test",
            """Thanks for your time. I purchased from another dealership that responded to
my request with direct written pricing without requiring a phone call or dealership
visit.""",
        )
    )

    return sorted(messages, key=lambda m: (m.sent_at, m.source_identifier))


def write(directory: Path) -> list[Path]:
    """Write the corpus out as .eml files. Idempotent."""
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.eml"):
        stale.unlink()

    written: list[Path] = []
    for index, message in enumerate(build()):
        key = (message.rfc822_message_id or "").strip("<>").split("@")[0]
        path = directory / f"{index:02d}-{key}.eml"
        path.write_bytes(eml.to_bytes(message))
        written.append(path)
    return written


def seed_dealers(db) -> dict[str, int]:
    """Create the dealerships. Contacts are left to be discovered by ingestion."""
    from app.ingestion.resolve import learn_domain
    from app.models import Dealer
    from app.services.states import ensure_states

    ensure_states(db)
    ids: dict[str, int] = {}
    for seed in DEALERS:
        dealer = Dealer(
            name=seed.name,
            city=seed.city,
            state=seed.state,
            distance_miles=seed.distance_miles,
            is_local=seed.is_local,
        )
        db.add(dealer)
        db.flush()
        learn_domain(db, dealer, seed.domain, verified=True)
        ids[seed.name] = dealer.id
    return ids


def seed_profile(db):
    """The buyer profile the replay runs against."""
    from app.models import BuyerProfile
    from app.services.money import to_cents

    profile = db.get(BuyerProfile, 1)
    if profile is None:
        profile = BuyerProfile(id=1)
        db.add(profile)
    profile.display_name = BUYER_NAME
    profile.email = BUYER
    profile.purchase_type = "NEW_ONLY"
    profile.target_year = 2026
    profile.target_make = "Honda"
    profile.target_model = "Civic Hatchback"
    profile.target_trim = "Sport"
    profile.target_powertrain = "NON_HYBRID"
    profile.color_preferences = "Dark or subdued"
    profile.excluded_colors = "white"
    profile.cash_available = True
    profile.financing_acceptable = "ONLY_IF_ADVANTAGEOUS"
    profile.wants_add_ons = False
    profile.wants_maintenance_plan = False
    profile.registration_state = "PA"
    profile.zip_code = "18435"
    profile.expected_tax_rate_bp = 600
    profile.local_dealer_premium_cents = to_cents("300.00")
    profile.avoid_phone_calls = True
    profile.avoid_dealership_visits = True
    db.flush()
    return profile
