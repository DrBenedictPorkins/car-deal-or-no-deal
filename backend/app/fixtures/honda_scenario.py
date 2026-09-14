"""The reference negotiation, as realistic seed data.

This is the real multi-dealer Honda Civic negotiation described in the brief, rebuilt
from interactions rather than hand-set summary fields. Loading it and running the
deterministic passes should reproduce the actual outcome:

* Stamford's final offer beats Westport by $275.09 OTD and $307.58 on dealer-controlled
  cost.
* Westport's quoted OTD is $100.00 more than its own line items add up to.
* Stamford improved its own opening offer by $806.21.
* The 2:14 PM call and the 4:03 PM quote contradict each other on add-ons.

Nothing here sets a price field directly on a dealer — every number arrives as an offer
attached to the interaction that carried it, exactly as it would from ingestion.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.enums import (
    ActorKind,
    Channel,
    CommitmentStatus,
    ContactRole,
    Direction,
    Party,
    Severity,
    SubjectType,
    VehicleCondition,
)
from app.models import (
    BuyerProfile,
    Campaign,
    Commitment,
    Contact,
    Dealer,
    Interaction,
    Question,
    Vehicle,
)
from app.models.base import utcnow
from app.services import facts as fact_service
from app.services import offers as offer_service
from app.services import state_engine
from app.services.money import to_cents as C
from app.services.states import ensure_states

BUYER_INQUIRY = """Hello,

I'm shopping for a new 2026 Honda Civic Hatchback Sport (non-hybrid), in a dark or
subdued colour — no white. I'm paying cash and registering in Pennsylvania (ZIP 18435),
no trade-in.

I'd like an itemized out-the-door price in writing for a specific VIN: selling price,
every dealer fee, any add-ons, plus tax and registration as separate lines. I don't want
any dealer add-ons or maintenance packages.

I'm contacting several dealers and will buy from whoever sends the best written number.
Email is best for me.

Thanks."""


def _interaction(
    db: Session,
    *,
    dealer: Dealer,
    contact: Contact | None,
    when: datetime,
    channel: Channel,
    direction: Direction,
    subject: str,
    body: str,
    actor_kind: ActorKind = ActorKind.UNKNOWN,
    classification_reason: str | None = None,
    quote_bearing: bool = False,
    vehicle: Vehicle | None = None,
) -> Interaction:
    row = Interaction(
        dealer_id=dealer.id,
        contact_id=contact.id if contact else None,
        vehicle_id=vehicle.id if vehicle else None,
        channel=channel,
        direction=direction,
        occurred_at=when,
        subject=subject,
        raw_content=body,
        normalized_content=body,
        actor_kind=actor_kind,
        classification_reason=classification_reason,
        is_quote_bearing=quote_bearing,
        source_system="fixture",
        source_identifier=f"fixture:{dealer.id}:{when.isoformat()}:{direction}",
    )
    db.add(row)
    db.flush()
    return row


def load(db: Session, *, base_time: datetime | None = None, accepted: bool = True) -> dict:
    """Load the scenario. ``base_time`` anchors day 0 of the negotiation."""
    ensure_states(db)
    base = base_time or (utcnow() - timedelta(days=12))

    def day(offset: float, hour: int = 9, minute: int = 0) -> datetime:
        return (base + timedelta(days=offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

    # ------------------------------------------------------------ profile
    profile = db.get(BuyerProfile, 1)
    if profile is None:
        profile = BuyerProfile(id=1)
        db.add(profile)
    profile.display_name = "Buyer"
    profile.purchase_type = "NEW_ONLY"
    profile.target_year = 2026
    profile.target_make = "Honda"
    profile.target_model = "Civic Hatchback"
    profile.target_trim = "Sport"
    profile.target_powertrain = "NON_HYBRID"
    profile.color_preferences = "Dark or subdued"
    profile.excluded_colors = "white"
    profile.has_trade_in = False
    profile.cash_available = True
    profile.financing_acceptable = "ONLY_IF_ADVANTAGEOUS"
    profile.wants_add_ons = False
    profile.wants_maintenance_plan = False
    profile.registration_state = "PA"
    profile.zip_code = "18435"
    profile.expected_tax_rate_bp = 600  # PA 6%
    profile.local_dealer_premium_cents = C("300.00")
    profile.avoid_phone_calls = True
    profile.avoid_dealership_visits = True
    profile.notes = "Email only unless there is a reason. Buying from the best written offer."

    campaign = Campaign(
        name="2026 Civic Hatchback Sport",
        target_description="New 2026 Honda Civic Hatchback Sport, non-hybrid, dark colour",
        opened_at=day(0),
    )
    db.add(campaign)
    db.flush()

    made: dict[str, Dealer] = {}

    def dealer(
        name: str, city: str, state: str, *, local: bool = False, miles: float | None = None
    ) -> Dealer:
        row = Dealer(
            name=name,
            city=city,
            state=state,
            is_local=local,
            distance_miles=miles,
            campaign_id=campaign.id,
            email_domains=name.lower().replace(" ", "") + ".example",
        )
        db.add(row)
        db.flush()
        made[name] = row
        return row

    def contact(
        d: Dealer,
        name: str,
        role: ContactRole,
        *,
        title: str | None = None,
        automated: ActorKind = ActorKind.HUMAN,
        primary: bool = False,
        evidence: str | None = None,
    ) -> Contact:
        row = Contact(
            dealer_id=d.id,
            name=name,
            title=title,
            role=role,
            actor_kind=automated,
            automation_evidence=evidence,
            is_primary=primary,
            email=f"{name.split()[0].lower()}@{d.email_domains}",
        )
        db.add(row)
        db.flush()
        return row

    # =================================================================
    # Honda of Westport — the clean written benchmark
    # =================================================================
    westport = dealer("Honda of Westport", "Westport", "CT", miles=62)
    chris = contact(
        westport, "Chris Benton", ContactRole.INTERNET_SALES, title="Internet Sales", primary=True
    )
    wp_vehicle = Vehicle(
        dealer_id=westport.id,
        campaign_id=campaign.id,
        year=2026,
        make="Honda",
        model="Civic Hatchback",
        trim="Sport",
        body_style="Hatchback",
        exterior_color="Urban Gray",
        interior_color="Black",
        mileage=16,
        condition=VehicleCondition.NEW,
        is_demo=False,
        is_loaner=False,
        damage_history="None reported",
    )
    db.add(wp_vehicle)
    db.flush()

    _interaction(
        db,
        dealer=westport,
        contact=chris,
        when=day(0, 9, 12),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="2026 Civic Hatchback Sport — itemized out-the-door request",
        body=BUYER_INQUIRY,
    )
    wp_quote = _interaction(
        db,
        dealer=westport,
        contact=chris,
        vehicle=wp_vehicle,
        when=day(1, 11, 24),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Re: 2026 Civic Hatchback Sport — itemized out-the-door request",
        actor_kind=ActorKind.HUMAN,
        classification_reason="Answers the specific questions asked; no template markers.",
        quote_bearing=True,
        body="""Good morning,

Happy to put this in writing. We have a 2026 Civic Sport Hatchback in Urban Gray with a
black interior, 16 miles on it. It is a new car — not a demo, not a loaner, no damage
reported.

Selling price       $28,035.00
Dealer fee          $699.00
Registration        $253.00
PA sales tax        $1,688.10
------------------------------
Out the door        $30,775.10

No add-ons on this one and no financing requirement — that's the cash price. Let me know
if you'd like me to hold it.

Chris Benton
Internet Sales""",
    )
    offer_service.create(
        db,
        {
            "dealer_id": westport.id,
            "vehicle_id": wp_vehicle.id,
            "interaction_id": wp_quote.id,
            "campaign_id": campaign.id,
            "quoted_at": day(1, 11, 24),
            "selling_price_cents": C("28035.00"),
            "doc_fee_cents": C("699.00"),
            "registration_cents": C("253.00"),
            "tax_cents": C("1688.10"),
            # Quoted by the dealer. Deliberately left to disagree with the line items by
            # $100 — that gap is real, and the engine should surface it rather than
            # quietly balance the books.
            "quoted_otd_cents": C("30775.10"),
            "financing_required": False,
            "notes": "Written cash quote, no add-ons.",
        },
        [],
    )
    for attribute, value, quote in (
        ("vehicle.is_demo", False, "It is a new car — not a demo, not a loaner"),
        ("vehicle.is_loaner", False, "not a loaner"),
        ("vehicle.damage_history", "None reported", "no damage reported"),
        ("offer.financing_required", False, "no financing requirement — that's the cash price"),
    ):
        kwargs = {"value_bool": value} if isinstance(value, bool) else {"value_text": value}
        fact_service.record(
            db,
            subject_type=SubjectType.VEHICLE if attribute.startswith("vehicle") else
            SubjectType.OFFER,
            subject_id=wp_vehicle.id,
            attribute=attribute,
            dealer_id=westport.id,
            interaction_id=wp_quote.id,
            quote=quote,
            observed_at=day(1, 11, 24),
            asserted_by_party=Party.DEALER,
            **kwargs,
        )
    fact_service.record(
        db,
        subject_type=SubjectType.VEHICLE,
        subject_id=wp_vehicle.id,
        attribute="vehicle.mileage",
        dealer_id=westport.id,
        value_number=16,
        value_unit="miles",
        interaction_id=wp_quote.id,
        quote="16 miles on it",
        observed_at=day(1, 11, 24),
    )

    # =================================================================
    # Honda of Stamford — the local dealer that ultimately won
    # =================================================================
    stamford = dealer("Honda of Stamford", "Stamford", "CT", local=True, miles=41)
    edwin = contact(
        stamford,
        "Edwin Sanchez",
        ContactRole.SALES_MANAGER,
        title="Sales Manager",
        primary=True,
    )
    st_vehicle = Vehicle(
        dealer_id=stamford.id,
        campaign_id=campaign.id,
        year=2026,
        make="Honda",
        model="Civic Hatchback",
        trim="Sport",
        body_style="Hatchback",
        vin="19XFL2H81TE040705",
        exterior_color="Meteorite Gray",
        interior_color="Black",
        mileage=10,
        msrp_cents=C("29090.00"),
        condition=VehicleCondition.NEW,
        is_demo=False,
    )
    db.add(st_vehicle)
    db.flush()

    _interaction(
        db,
        dealer=stamford,
        contact=edwin,
        when=day(0, 9, 12),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="2026 Civic Hatchback Sport — itemized out-the-door request",
        body=BUYER_INQUIRY,
    )

    # The call that later contradicts the written quote.
    st_call = _interaction(
        db,
        dealer=stamford,
        contact=edwin,
        when=day(1, 14, 14),
        channel=Channel.CALL,
        direction=Direction.INBOUND,
        subject="Inbound call from Edwin Sanchez (7 min)",
        actor_kind=ActorKind.HUMAN,
        body="""[00:00] Edwin: Hi, this is Edwin over at Honda of Stamford, calling about the
Civic Sport Hatchback you inquired on.
[00:41] Buyer: Thanks. Before we go further — do you add anything to the car? I don't
want etching, coatings, any of that.
[00:52] Edwin: No, there are no mandatory dealer add-ons here. What you see is the car.
[01:10] Buyer: Good. Can you send the whole thing in writing, itemized, out the door?
[01:18] Edwin: Absolutely, I'll email it over this afternoon.""",
    )
    fact_service.record(
        db,
        subject_type=SubjectType.DEALER,
        subject_id=stamford.id,
        attribute="dealer.no_mandatory_add_ons",
        dealer_id=stamford.id,
        value_bool=True,
        interaction_id=st_call.id,
        quote="No, there are no mandatory dealer add-ons here.",
        observed_at=day(1, 14, 14),
        asserted_by_party=Party.DEALER,
    )
    db.add(
        Commitment(
            dealer_id=stamford.id,
            party=Party.DEALER,
            interaction_id=st_call.id,
            text="Send the itemized out-the-door quote in writing this afternoon.",
            due_at=day(1, 18, 0),
            status=CommitmentStatus.KEPT,
        )
    )

    st_quote1 = _interaction(
        db,
        dealer=stamford,
        contact=edwin,
        vehicle=st_vehicle,
        when=day(1, 16, 3),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Your Civic Sport Hatchback — numbers",
        actor_kind=ActorKind.HUMAN,
        quote_bearing=True,
        body="""As promised:

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
    )
    offer_service.create(
        db,
        {
            "dealer_id": stamford.id,
            "vehicle_id": st_vehicle.id,
            "interaction_id": st_quote1.id,
            "campaign_id": campaign.id,
            "quoted_at": day(1, 16, 3),
            "msrp_cents": C("29090.00"),
            "selling_price_cents": C("28090.00"),
            "doc_fee_cents": C("699.00"),
            "other_non_tax_fees_cents": C("368.00"),
            "tax_cents": C("1751.22"),
            "quoted_otd_cents": C("31306.22"),
            "financing_required": False,
        },
        [
            {
                "kind": "ADD_ON",
                "name": "VIN Etching",
                "price_cents": C("269.00"),
                "is_taxable": True,
                "mandatory_claimed": True,
                "user_wants": False,
            },
            {
                "kind": "ADD_ON",
                "name": "Wheel Locks",
                "price_cents": C("129.00"),
                "is_taxable": True,
                "mandatory_claimed": True,
                "already_installed": True,
                "user_wants": False,
            },
        ],
    )
    for attribute, kwargs, quote in (
        ("vehicle.vin", {"value_text": "19XFL2H81TE040705"}, "VIN 19XFL2H81TE040705"),
        ("vehicle.is_demo", {"value_bool": False}, "Brand new, not a demo"),
        ("vehicle.mileage", {"value_number": 10, "value_unit": "miles"}, "10 miles"),
    ):
        fact_service.record(
            db,
            subject_type=SubjectType.VEHICLE,
            subject_id=st_vehicle.id,
            attribute=attribute,
            dealer_id=stamford.id,
            interaction_id=st_quote1.id,
            quote=quote,
            observed_at=day(1, 16, 3),
            **kwargs,
        )

    _interaction(
        db,
        dealer=stamford,
        contact=edwin,
        when=day(2, 10, 5),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="Re: Your Civic Sport Hatchback — numbers",
        body="""Edwin,

Thanks. Two things.

On the call you told me there were no mandatory dealer add-ons, but the quote has VIN
etching at $269 and wheel locks at $129 — $398 I didn't ask for and don't want.

Separately, I have a written offer from another Honda store at $30,775.10 out the door
on the same car, no add-ons. I'd rather buy locally from you. If you can beat that
number I'll commit today.""",
    )

    st_quote2 = _interaction(
        db,
        dealer=stamford,
        contact=edwin,
        vehicle=st_vehicle,
        when=day(3, 15, 40),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Re: Your Civic Sport Hatchback — revised",
        actor_kind=ActorKind.HUMAN,
        quote_bearing=True,
        body="""I took this to my GM. Here's where we can land:

MSRP                    $29,090.00
Adjusted selling price  $27,329.42
VIN etching             $269.00
Wheel locks             $129.00
Doc fee                 $699.00
Sales tax               $1,705.59
Non-tax fees            $368.00
----------------------------------
Out the door            $30,500.01

That beats the offer you sent me and you're buying local. The etch and locks are already
on the car so I can't strip them, but I've taken the money out of the selling price to
cover them.

Edwin""",
    )
    offer_service.create(
        db,
        {
            "dealer_id": stamford.id,
            "vehicle_id": st_vehicle.id,
            "interaction_id": st_quote2.id,
            "campaign_id": campaign.id,
            "quoted_at": day(3, 15, 40),
            "msrp_cents": C("29090.00"),
            "selling_price_cents": C("27329.42"),
            "doc_fee_cents": C("699.00"),
            "other_non_tax_fees_cents": C("368.00"),
            "tax_cents": C("1705.59"),
            "quoted_otd_cents": C("30500.01"),
            "financing_required": False,
            "notes": "Final. Beat Westport's written offer by $275.09.",
        },
        [
            {
                "kind": "ADD_ON",
                "name": "VIN Etching",
                "price_cents": C("269.00"),
                "is_taxable": True,
                "mandatory_claimed": True,
                "already_installed": True,
                "removable_confirmed": False,
                "user_wants": False,
            },
            {
                "kind": "ADD_ON",
                "name": "Wheel Locks",
                "price_cents": C("129.00"),
                "is_taxable": True,
                "mandatory_claimed": True,
                "already_installed": True,
                "removable_confirmed": False,
                "user_wants": False,
            },
        ],
    )

    # =================================================================
    # Curry Honda — competitive number, but gated behind a phone call
    # =================================================================
    curry = dealer("Curry Honda", "Scarsdale", "NY", miles=78)
    jessica = contact(
        curry, "Jessica Rivera", ContactRole.INTERNET_SALES, title="Internet Sales", primary=True
    )
    curry_vehicle = Vehicle(
        dealer_id=curry.id,
        campaign_id=campaign.id,
        year=2026,
        make="Honda",
        model="Civic Hatchback",
        trim="Sport",
        condition=VehicleCondition.NEW,
    )
    db.add(curry_vehicle)
    db.flush()
    _interaction(
        db,
        dealer=curry,
        contact=jessica,
        when=day(0, 9, 12),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="2026 Civic Hatchback Sport — itemized out-the-door request",
        body=BUYER_INQUIRY,
    )
    curry_reply = _interaction(
        db,
        dealer=curry,
        contact=jessica,
        when=day(1, 13, 2),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Re: 2026 Civic Hatchback Sport",
        actor_kind=ActorKind.HUMAN,
        quote_bearing=True,
        body="""Hi! Great news, we have Sport Hatchbacks in stock.

Our advertised price is $27,954 which includes our $175 doc fee. That price does require
financing through Honda Financial — 7.14% for 84 months.

For the final out-the-door number my manager John has to sign off, so give me a call or
let me know the best time to reach you and I'll have him walk you through it.

Jessica Rivera""",
    )
    offer_service.create(
        db,
        {
            "dealer_id": curry.id,
            "vehicle_id": curry_vehicle.id,
            "interaction_id": curry_reply.id,
            "campaign_id": campaign.id,
            "quoted_at": day(1, 13, 2),
            "advertised_price_cents": C("27954.00"),
            "selling_price_cents": C("27779.00"),
            "doc_fee_cents": C("175.00"),
            "financing_required": True,
            "financing_provider": "Honda Financial",
            "apr_bp": 714,
            "financing_term_months": 84,
            # Left NULL on purpose: these are exactly the terms that must be confirmed
            # before anyone suggests financing and paying the loan off immediately.
            "prepayment_penalty": None,
            "discount_clawback": None,
            "minimum_financed_cents": None,
            "minimum_loan_months": None,
            "notes": "Advertised price only. No tax, registration or OTD provided.",
        },
        [],
    )
    db.add(
        Question(
            dealer_id=curry.id,
            interaction_id=curry_reply.id,
            asked_by=Party.BUYER,
            text="Can you send the full itemized out-the-door figure by email without a call?",
            topic="pricing",
            importance=Severity.WARNING,
            asked_at=day(1, 17, 30),
        )
    )
    db.add(
        Question(
            dealer_id=curry.id,
            interaction_id=curry_reply.id,
            asked_by=Party.BUYER,
            text="Is there a prepayment penalty, minimum term, or discount clawback on the "
            "Honda Financial requirement?",
            topic="financing",
            importance=Severity.CRITICAL,
            asked_at=day(1, 17, 30),
        )
    )
    _interaction(
        db,
        dealer=curry,
        contact=jessica,
        when=day(1, 17, 30),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="Re: 2026 Civic Hatchback Sport",
        body="""Thanks Jessica. I'd rather not do this by phone — could you email the full
itemized out-the-door figure?

Also, on the financing requirement: is there a prepayment penalty, a minimum number of
payments, a minimum amount financed, or a discount clawback if the loan is paid off
early?""",
    )

    # =================================================================
    # Ocean Honda Milford — engaged, countered, wants a visit
    # =================================================================
    ocean = dealer("Ocean Honda Milford", "Milford", "CT", miles=88)
    patrick = contact(
        ocean, "Patrick Mackin", ContactRole.SALESPERSON, title="Sales Consultant", primary=True
    )
    ocean_vehicle = Vehicle(
        dealer_id=ocean.id,
        campaign_id=campaign.id,
        year=2026,
        make="Honda",
        model="Civic Hatchback",
        trim="Sport",
        msrp_cents=C("29320.00"),
        condition=VehicleCondition.NEW,
    )
    db.add(ocean_vehicle)
    db.flush()
    _interaction(
        db,
        dealer=ocean,
        contact=patrick,
        when=day(0, 9, 12),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="2026 Civic Hatchback Sport — itemized out-the-door request",
        body=BUYER_INQUIRY,
    )
    ocean_reply = _interaction(
        db,
        dealer=ocean,
        contact=patrick,
        when=day(2, 9, 48),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Re: 2026 Civic Hatchback Sport",
        actor_kind=ActorKind.HUMAN,
        quote_bearing=True,
        body="""Thanks for reaching out. MSRP on the Sport Hatchback is $29,320.

I went to my manager and got approval for $500 off, so $28,820 selling price. If we get
close on numbers, would you be able to come in so we can finalise? We do better when
people come down.

Patrick Mackin""",
    )
    offer_service.create(
        db,
        {
            "dealer_id": ocean.id,
            "vehicle_id": ocean_vehicle.id,
            "interaction_id": ocean_reply.id,
            "campaign_id": campaign.id,
            "quoted_at": day(2, 9, 48),
            "msrp_cents": C("29320.00"),
            "selling_price_cents": C("28820.00"),
            "discount_cents": C("500.00"),
            "notes": "Selling price only — no fees, tax or OTD provided.",
        },
        [],
    )
    _interaction(
        db,
        dealer=ocean,
        contact=patrick,
        when=day(2, 16, 20),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="Re: 2026 Civic Hatchback Sport",
        body="""Thanks Patrick. $28,820 isn't there yet — I'm working from written offers
around $28,000 selling.

I'll do $27,750 with no add-ons, and I can close by email today. Could you send the
itemized out-the-door figure at that price?""",
    )
    db.add(
        Question(
            dealer_id=ocean.id,
            asked_by=Party.BUYER,
            text="Will you do $27,750 with no add-ons, quoted out the door in writing?",
            topic="pricing",
            importance=Severity.WARNING,
            asked_at=day(2, 16, 20),
        )
    )

    # =================================================================
    # White Plains Honda — replied, no numbers, wants the phone
    # =================================================================
    white_plains = dealer("White Plains Honda", "White Plains", "NY", miles=71)
    wp_contact = contact(
        white_plains, "Dana Whitfield", ContactRole.BDC, title="Business Development", primary=True
    )
    _interaction(
        db,
        dealer=white_plains,
        contact=wp_contact,
        when=day(0, 9, 12),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="2026 Civic Hatchback Sport — itemized out-the-door request",
        body=BUYER_INQUIRY,
    )
    _interaction(
        db,
        dealer=white_plains,
        contact=wp_contact,
        when=day(1, 8, 15),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Re: 2026 Civic Hatchback Sport",
        actor_kind=ActorKind.HUMAN,
        body="""Thank you for your interest in White Plains Honda! We do have Civic Sport
Hatchbacks available.

What's the best number to reach you at, and what's a good time? Give me a call and I can
go over everything with you.

Dana""",
    )

    # =================================================================
    # Mount Kisco Honda — two contacts, no price, phone pressure
    # =================================================================
    kisco = dealer("Mount Kisco Honda", "Mount Kisco", "NY", miles=84)
    amber = contact(
        kisco, "Amber Chase", ContactRole.CLIENT_SERVICES, title="Client Services", primary=True
    )
    james = contact(kisco, "James Woods", ContactRole.SALES_MANAGER, title="Sales Manager")
    _interaction(
        db,
        dealer=kisco,
        contact=amber,
        when=day(0, 9, 12),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="2026 Civic Hatchback Sport — itemized out-the-door request",
        body=BUYER_INQUIRY,
    )
    kisco_reply = _interaction(
        db,
        dealer=kisco,
        contact=amber,
        when=day(0, 15, 40),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Re: 2026 Civic Hatchback Sport",
        actor_kind=ActorKind.HUMAN,
        body="""Hi, thanks for contacting Mount Kisco Honda. I've passed this to our sales
manager James, who will put together pricing for you today.

Amber Chase
Client Services""",
    )
    db.add(
        Commitment(
            dealer_id=kisco.id,
            party=Party.DEALER,
            interaction_id=kisco_reply.id,
            text="Sales manager will put together pricing today.",
            due_at=day(0, 21, 0),
            status=CommitmentStatus.OPEN,
        )
    )
    _interaction(
        db,
        dealer=kisco,
        contact=james,
        when=day(2, 11, 5),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Civic Sport Hatchback",
        actor_kind=ActorKind.HUMAN,
        body="""Good morning, James here, sales manager at Mount Kisco Honda.

I'd love to earn your business. Rather than go back and forth over email, what's the
best time to reach you today? Give me a call and I'll make sure we take care of you.

James Woods""",
    )

    # =================================================================
    # Tarrytown Honda — visit required, automated follow-ups, closed out
    # =================================================================
    tarrytown = dealer("Tarrytown Honda", "Tarrytown", "NY", miles=69)
    ebony = contact(
        tarrytown, "Ebony Bryant", ContactRole.CLIENT_SERVICES, title="Client Services",
        primary=True,
    )
    corey = contact(
        tarrytown,
        "Corey Smith",
        ContactRole.SALES_MANAGER,
        title="Sales Manager",
        automated=ActorKind.AUTOMATED,
        evidence=(
            "Follow-ups restate the buyer's original inquiry almost verbatim and arrive "
            "on a fixed cadence; sent through a lead-management system rather than "
            "composed as replies."
        ),
    )
    _interaction(
        db,
        dealer=tarrytown,
        contact=ebony,
        when=day(0, 9, 12),
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        subject="2026 Civic Hatchback Sport — itemized out-the-door request",
        body=BUYER_INQUIRY,
    )
    _interaction(
        db,
        dealer=tarrytown,
        contact=ebony,
        when=day(0, 18, 2),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Re: 2026 Civic Hatchback Sport",
        actor_kind=ActorKind.HUMAN,
        body="""Thank you for contacting Tarrytown Honda.

To guarantee you our absolute best price we'd need you to come in and see the vehicle in
person. Our manager can then put together his best offer for you.

Ebony Bryant
Client Services""",
    )
    _interaction(
        db,
        dealer=tarrytown,
        contact=corey,
        when=day(1, 9, 1),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Following up on your 2026 Honda Civic Hatchback Sport inquiry",
        actor_kind=ActorKind.AUTOMATED,
        classification_reason=(
            "Body restates the buyer's own inquiry verbatim; no answer to any question "
            "asked; fixed-cadence lead-management follow-up."
        ),
        body="""I see you were looking for a new 2026 Honda Civic Hatchback Sport (non-hybrid),
in a dark or subdued colour — no white, paying cash, registering in Pennsylvania.

I'd love to help! When can you come in?

Corey Smith
Sales Manager""",
    )
    _interaction(
        db,
        dealer=tarrytown,
        contact=corey,
        when=day(3, 9, 1),
        channel=Channel.EMAIL,
        direction=Direction.INBOUND,
        subject="Following up on your 2026 Honda Civic Hatchback Sport inquiry",
        actor_kind=ActorKind.AUTOMATED,
        classification_reason="Identical template to the previous follow-up, 48h later.",
        body="""I see you were looking for a new 2026 Honda Civic Hatchback Sport (non-hybrid),
in a dark or subdued colour — no white, paying cash, registering in Pennsylvania.

I'd love to help! When can you come in?

Corey Smith
Sales Manager""",
    )

    db.flush()

    # ---------------------------------------------------------- outcome
    if accepted:
        _interaction(
            db,
            dealer=stamford,
            contact=edwin,
            when=day(4, 9, 30),
            channel=Channel.EMAIL,
            direction=Direction.OUTBOUND,
            subject="Re: Your Civic Sport Hatchback — revised",
            body="""Edwin — that works. $30,500.01 out the door on VIN 19XFL2H81TE040705,
Meteorite Gray. Let's do it. Send me the buyer's order and let me know when I can pick
it up.""",
        )
        _interaction(
            db,
            dealer=tarrytown,
            contact=ebony,
            when=day(4, 10, 0),
            channel=Channel.EMAIL,
            direction=Direction.OUTBOUND,
            subject="Re: 2026 Civic Hatchback Sport",
            body="""Thanks for your time. I purchased from another dealership that responded to
my request with direct written pricing without requiring a phone call or dealership
visit.""",
        )
        db.flush()
        state_engine.set_state_manually(
            db, made["Honda of Stamford"], "ACCEPTED", reason="Buyer accepted the revised offer."
        )
        state_engine.set_state_manually(
            db, made["Tarrytown Honda"], "CLOSED", reason="Buyer closed this dealer out."
        )
        state_engine.set_state_manually(
            db, made["Honda of Westport"], "FINALIST", reason="Strongest clean written benchmark."
        )

    state_engine.refresh_all(db)
    db.flush()
    return {name: d.id for name, d in made.items()}
