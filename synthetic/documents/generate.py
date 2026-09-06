"""Generating the document corpus and its ground truth (docs/10 §6-§7).

Each application gets a bundle of documents rendered from the population's own
figures, so a payslip agrees with the salary the member actually earns and a
deduction record agrees with what the employer actually remitted. Anomalies are
injected deliberately and listed in a manifest, so T-024's forensics can be
scored against a known answer.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from synthetic.config import DEMO_AS_OF_MONTH, HISTORY_START, Settings
from synthetic.documents.anomalies import INJECTED_SHARE, Anomaly, AnomalyKind
from synthetic.documents.noise import ScanProfile, apply_scan_noise, clean_digital_share
from synthetic.documents.render import CARD_VIEWPORT, Renderer
from synthetic.population.calendar import cycle_label, month_end
from synthetic.writer import read_table

__all__ = ["DOCUMENT_TYPES", "GeneratedDocument", "generate_documents"]

DOCUMENT_TYPES = (
    "IDENTITY",
    "PAYSLIP_LATEST_3",
    "EMPLOYMENT_CONFIRMATION",
    "BANK_STATEMENT_3M",
    "PROVIDENT_FUND_STATEMENT",
)

#: Which required document goes missing when a bundle is incomplete.
_DROPPABLE = ("PAYSLIP_LATEST_3", "EMPLOYMENT_CONFIRMATION", "BANK_STATEMENT_3M")

_PURPOSES = ("PERSONAL", "EDUCATION", "MEDICAL", "HOME_IMPROVEMENT", "DEBT_CONSOLIDATION", "VEHICLE")

_POSITIONS = (
    "Operations Officer",
    "Senior Technician",
    "Administrator",
    "Field Supervisor",
    "Analyst",
    "Coordinator",
    "Team Leader",
)


@dataclass(slots=True)
class GeneratedDocument:
    document_id: str
    application_id: str
    member_id: str
    type: str
    template_id: str | None
    filename: str
    ground_truth: dict[str, Any]
    boxes: dict[str, list[float]]
    scan: ScanProfile
    sha256: str = ""
    phash: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "application_id": self.application_id,
            "member_id": self.member_id,
            "type": self.type,
            "template_id": self.template_id,
            "filename": self.filename,
            "clean_digital": self.scan.is_clean,
            "sha256": self.sha256,
            "phash": self.phash,
        }

    def truth_row(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "type": self.type,
            "fields": self.ground_truth,
            "boxes": self.boxes,
        }


@dataclass
class Corpus:
    applications: list[dict[str, Any]] = field(default_factory=list)
    documents: list[GeneratedDocument] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _plain(value: str) -> float:
    return float(value.replace(",", ""))


def _periods(as_of_month: int, count: int = 3) -> list[str]:
    """The latest `count` pay periods, most recent first."""
    return [cycle_label(HISTORY_START, as_of_month - offset) for offset in range(count)]


def _payslip_context(
    member: dict[str, Any],
    employer: dict[str, Any],
    net_by_period: dict[str, float],
    rng: np.random.Generator,
    *,
    template_id: str,
    net_style: str = "",
) -> dict[str, Any]:
    salary = float(member["salary_monthly"])
    periods = []
    for label, net in net_by_period.items():
        allowance = round(salary * 0.05, 2)
        gross = round(salary + allowance, 2)
        periods.append(
            {
                "period": label,
                "pay_date": f"{label}-26",
                "gross_salary": _money(gross),
                "net_salary": _money(net),
                "total_deductions": _money(round(gross - net, 2)),
                "allowances": [{"label": "Transport", "amount": _money(allowance)}],
                "deductions": [
                    {"label": "Pension", "amount": _money(round(salary * 0.08, 2))},
                    {"label": "Cooperative deduction", "amount": _money(round(salary * 0.25, 2))},
                ],
            }
        )
    return {
        "employer_name": employer["name"],
        "template_id": template_id,
        "employee_name": member["name_token"],
        "staff_number": f"SN-{member['member_id'][2:]}",
        "currency": "LCU",
        "net_style": net_style,
        "periods": periods,
    }


def _identity_context(
    member: dict[str, Any], rng: np.random.Generator, *, id_number: str | None = None, name: str | None = None
) -> dict[str, Any]:
    dob = date.fromisoformat(member["dob"])
    return {
        "name": name or member["name_token"],
        "id_number": id_number or _id_number(member["member_id"]),
        "dob": dob.isoformat(),
        "expiry": (dob + timedelta(days=int(60 * 365.25))).isoformat(),
        "avatar": _avatar(member["member_id"]),
    }


#: A card without a photograph is not a card, and without one two members'
#: cards are very nearly the same image. Hues are continuous rather than drawn
#: from a small palette, so distinct members produce distinct pixels.
_SKIN = (
    "#e8c39e",
    "#c98f63",
    "#8d5524",
    "#f1d5b8",
    "#6b4423",
    "#a9714b",
    "#d9a066",
    "#7a4a2b",
    "#eabd9c",
    "#5c3a21",
)


def _avatar(member_id: str) -> dict[str, Any]:
    """A stable, distinguishable photograph for one member."""
    digits = int(member_id[2:])
    return {
        "skin": _SKIN[digits % len(_SKIN)],
        "clothing": f"hsl({(digits * 37) % 360}, {28 + digits % 34}%, {26 + digits % 22}%)",
        "hair_colour": f"hsl({(digits * 53) % 60}, {18 + digits % 40}%, {12 + digits % 26}%)",
        "background": f"hsl({(digits * 71) % 360}, {10 + digits % 18}%, {74 + digits % 14}%)",
        "head_y": 21 + digits % 9,
        "head_r": 12 + digits % 7,
        "hair": 5 + digits % 12,
        "shoulder": 13 + digits % 9,
    }


def _id_number(member_id: str) -> str:
    """`AB1234567`, deterministic per member (docs/10 §6)."""
    digits = member_id[2:]
    letters = chr(65 + int(digits[0]) % 26) + chr(65 + int(digits[1]) % 26)
    return f"{letters}{digits[-7:].rjust(7, '0')}"


def generate_documents(
    applications_wanted: int = 600,
    *,
    population_dir: Path,
    out: Path,
    settings: Settings | None = None,
    seed: int = 42,
    as_of_month: int = DEMO_AS_OF_MONTH,
    render_pdf: bool = True,
) -> Corpus:
    """Render a document bundle for each sampled application."""
    settings = settings or Settings(seed=seed)
    rng = np.random.default_rng(seed + 1_000)

    members = read_table("member", population_dir)
    employers = {e["employer_id"]: e for e in read_table("employer", population_dir)}
    deductions = read_table("deduction", population_dir)

    if not members:
        raise FileNotFoundError(f"no population in {population_dir}; run `synthetic.cli population` first")

    # The net pay the employer reported for each cycle. The payslip is printed
    # from this, so payslip and deduction record agree unless an anomaly is
    # injected, which is what makes docs/07 §1.5's comparison meaningful.
    reported_net: dict[tuple[str, str], float] = {
        (row["member_id"], row["cycle"]): float(row["net_salary"])
        for row in deductions
        if row.get("net_salary") is not None
    }

    chosen = rng.choice(len(members), size=min(applications_wanted, len(members)), replace=False)
    periods = _periods(as_of_month)

    files = out / "files"
    files.mkdir(parents=True, exist_ok=True)

    corpus = Corpus()
    reusable_statement: bytes | None = None
    reusable_member: str | None = None
    counter = 0

    with Renderer() as renderer:
        for index, position in enumerate(chosen.tolist()):
            member = members[position]
            employer = employers[member["employer_id"]]
            salary = float(member["salary_monthly"])
            application_id = f"APP-{index + 1:05d}"

            corpus.applications.append(
                {
                    "application_id": application_id,
                    "member_id": member["member_id"],
                    "product_code": "PF-STD" if rng.random() < 0.8 else "PF-SHARIAH",
                    "amount": f"{round(salary * float(rng.uniform(0.5, 6.0)), 2):.2f}",
                    "tenor_months": int(rng.integers(12, 61)),
                    "purpose": str(rng.choice(_PURPOSES)),
                    "created_at": month_end(HISTORY_START, as_of_month).isoformat(),
                }
            )

            # --- bundle completeness (docs/10 §7) -------------------------
            roll = rng.random()
            missing: str | None = None
            force_poor_scan = False
            if roll < 0.10:
                missing = str(rng.choice(_DROPPABLE))
            elif roll < 0.15:
                force_poor_scan = True

            wanted = ["IDENTITY", "PAYSLIP_LATEST_3", "EMPLOYMENT_CONFIRMATION"]
            if rng.random() < 0.55:
                wanted.append("BANK_STATEMENT_3M")
            elif rng.random() < 0.30:
                wanted.append("PROVIDENT_FUND_STATEMENT")
            wanted = [w for w in wanted if w != missing]

            # --- which anomaly, if any ------------------------------------
            anomaly_kind: AnomalyKind | None = None
            if rng.random() < INJECTED_SHARE:
                anomaly_kind = AnomalyKind(
                    str(
                        rng.choice(
                            [
                                AnomalyKind.EDITED_TOTAL,
                                AnomalyKind.INCOME_VARIANCE,
                                AnomalyKind.METADATA_MISMATCH,
                                AnomalyKind.TEMPLATE_MISMATCH,
                                AnomalyKind.IDENTITY_MISMATCH,
                                AnomalyKind.DUPLICATE_ID,
                                AnomalyKind.REUSED_IMAGE,
                            ]
                        )
                    )
                )

            for document_type in wanted:
                counter += 1
                document_id = f"DOC-{counter:06d}"
                scan = (
                    ScanProfile.draw(rng)
                    if force_poor_scan or rng.random() >= clean_digital_share
                    else ScanProfile.clean()
                )

                built = _build(
                    document_type,
                    member,
                    employer,
                    salary,
                    periods,
                    reported_net,
                    rng,
                    renderer,
                    anomaly_kind,
                    application_id,
                    document_id,
                    reusable_statement,
                    reusable_member,
                )
                if built is None:
                    counter -= 1
                    continue
                result, truth, template_id, anomaly, image_override = built

                png = image_override or apply_scan_noise(result.png, scan)
                stem = f"{document_id}_{document_type.lower()}"
                (files / f"{stem}.png").write_bytes(png)
                if render_pdf:
                    (files / f"{stem}.pdf").write_bytes(result.pdf)

                document = GeneratedDocument(
                    document_id=document_id,
                    application_id=application_id,
                    member_id=member["member_id"],
                    type=document_type,
                    template_id=template_id,
                    filename=f"{stem}.png",
                    ground_truth=truth,
                    boxes=result.boxes,
                    scan=scan,
                )
                document.sha256 = _sha256(png)
                document.phash = _phash(png)
                corpus.documents.append(document)

                if anomaly is not None:
                    corpus.anomalies.append(
                        Anomaly(
                            kind=anomaly,
                            document_id=document_id,
                            application_id=application_id,
                            member_id=member["member_id"],
                            detail=truth.get("_anomaly", {}),
                        )
                    )

                # keep one statement image to reuse under a different member
                if document_type == "BANK_STATEMENT_3M" and reusable_statement is None:
                    reusable_statement = png
                    reusable_member = member["member_id"]

    _write(out, corpus)
    return corpus


def _build(
    document_type: str,
    member: dict[str, Any],
    employer: dict[str, Any],
    salary: float,
    periods: list[str],
    reported_net: dict[tuple[str, str], float],
    rng: np.random.Generator,
    renderer: Renderer,
    anomaly_kind: AnomalyKind | None,
    application_id: str,
    document_id: str,
    reusable_statement: bytes | None,
    reusable_member: str | None,
) -> tuple[Any, dict[str, Any], str | None, AnomalyKind | None, bytes | None] | None:
    """Render one document, applying the application's anomaly if it belongs here."""
    anomaly: AnomalyKind | None = None
    image_override: bytes | None = None
    template_id: str | None = None

    if document_type == "PAYSLIP_LATEST_3":
        net_by_period = {
            label: round(reported_net.get((member["member_id"], label), salary * 0.72), 2)
            for label in periods
        }

        net_style = ""
        detail: dict[str, Any] = {}

        edited_net: tuple[str, float] | None = None
        if anomaly_kind is AnomalyKind.EDITED_TOTAL:
            original = net_by_period[periods[0]]
            inflated = round(original * float(rng.uniform(1.08, 1.20)), 2)
            # A forger raises the net and re-typesets it. They do not recompute
            # the deduction total, so the page stops adding up. That arithmetic
            # break is what forensics detects, with the font change as support.
            edited_net = (periods[0], inflated)
            net_style = "font-family: 'Liberation Serif', serif; letter-spacing: .06em;"
            detail = {
                "original_net": original,
                "stated_net": inflated,
                "inflation": round(inflated / original - 1, 4),
                "arithmetic_breaks": True,
            }
            anomaly = anomaly_kind

        elif anomaly_kind is AnomalyKind.INCOME_VARIANCE:
            # payslip net sits 6 % below what the employer remitted (S2)
            for label in periods:
                net_by_period[label] = round(net_by_period[label] * 0.94, 2)
            detail = {"variance": 0.06, "direction": "payslip_below_deduction"}
            anomaly = anomaly_kind

        template_id = employer["template_id"]
        if anomaly_kind is AnomalyKind.TEMPLATE_MISMATCH:
            other = f"tpl-{(int(template_id.split('-')[1]) % 12) + 1:02d}"
            detail = {"employer_template": template_id, "rendered_template": other}
            template_id = other
            anomaly = anomaly_kind

        context = _payslip_context(
            member, employer, net_by_period, rng, template_id=template_id, net_style=net_style
        )
        if edited_net is not None:
            # overwrite only the printed net; the deduction total keeps the
            # value computed from the true figure
            label, inflated_value = edited_net
            for block in context["periods"]:
                if block["period"] == label:
                    block["net_salary"] = _money(inflated_value)

        result = renderer.render(f"payslip_{template_id}.html.j2", context)
        truth = {
            "employer_name": employer["name"],
            "employee_name": member["name_token"],
            "period": periods[0],
            "pay_date": f"{periods[0]}-26",
            "gross_salary": context["periods"][0]["gross_salary"],
            "net_salary": context["periods"][0]["net_salary"],
            "total_deductions": context["periods"][0]["total_deductions"],
            "net_salary_prior_1": context["periods"][1]["net_salary"],
            "net_salary_prior_2": context["periods"][2]["net_salary"],
            "period_prior_1": periods[1],
            "period_prior_2": periods[2],
            "staff_number": context["staff_number"],
        }
        if detail:
            truth["_anomaly"] = detail
        return result, truth, template_id, anomaly, None

    if document_type == "IDENTITY":
        id_number = _id_number(member["member_id"])
        name = member["name_token"]
        detail = {}

        if anomaly_kind is AnomalyKind.IDENTITY_MISMATCH:
            name = name.split()[0] + " " + "Mismatch"
            detail = {"record_name": member["name_token"], "document_name": name}
            anomaly = anomaly_kind
        elif anomaly_kind is AnomalyKind.DUPLICATE_ID:
            id_number = "ZZ0000001"
            detail = {"shared_id_number": id_number}
            anomaly = anomaly_kind

        context = _identity_context(member, rng, id_number=id_number, name=name)
        result = renderer.render("identity_card.html.j2", context, viewport=CARD_VIEWPORT)
        truth = {k: v for k, v in context.items() if k != "avatar"}
        if detail:
            truth["_anomaly"] = detail
        return result, truth, None, anomaly, None

    if document_type == "EMPLOYMENT_CONFIRMATION":
        letter_date = f"{periods[0]}-05"
        detail = {}
        if anomaly_kind is AnomalyKind.METADATA_MISMATCH:
            # letter dated before the period it certifies (INT-01)
            letter_date = f"{periods[2]}-01"
            detail = {"letter_date": letter_date, "claimed_period": periods[0]}
            anomaly = anomaly_kind

        context = {
            "employer_name": employer["name"],
            "employee_name": member["name_token"],
            "position": str(rng.choice(_POSITIONS)),
            "start_date": member["joined_at"],
            "monthly_salary": _money(salary),
            "letter_date": letter_date,
            "currency": "LCU",
        }
        result = renderer.render("employment_letter.html.j2", context)
        truth = {k: v for k, v in context.items() if k != "currency"}
        if detail:
            truth["_anomaly"] = detail
        return result, truth, None, anomaly, None

    if document_type == "BANK_STATEMENT_3M":
        balance = salary * float(rng.uniform(0.5, 3.0))
        transactions = []
        for label in reversed(periods):
            credit = round(salary * 0.72, 2)
            balance += credit
            transactions.append(
                {
                    "date": f"{label}-26",
                    "description": "Salary credit",
                    "debit": "",
                    "credit": _money(credit),
                    "balance": _money(balance),
                    "is_salary": True,
                }
            )
            spend = round(salary * float(rng.uniform(0.3, 0.6)), 2)
            balance -= spend
            transactions.append(
                {
                    "date": f"{label}-28",
                    "description": "Card purchases",
                    "debit": _money(spend),
                    "credit": "",
                    "balance": _money(balance),
                    "is_salary": False,
                }
            )

        context = {
            "bank_name": "Meridian Savings Bank",
            "issued_on": f"{periods[0]}-28",
            "account_holder": member["name_token"],
            "account_number_masked": f"****{member['member_id'][-4:]}",
            "period": f"{periods[2]} to {periods[0]}",
            "currency": "LCU",
            "transactions": transactions,
            "closing_balance": _money(balance),
        }
        result = renderer.render("bank_statement.html.j2", context)
        truth = {
            "account_holder": context["account_holder"],
            "account_number_masked": context["account_number_masked"],
            "period": context["period"],
            "closing_balance": context["closing_balance"],
        }

        if (
            anomaly_kind is AnomalyKind.REUSED_IMAGE
            and reusable_statement is not None
            and reusable_member != member["member_id"]
        ):
            image_override = reusable_statement
            truth["_anomaly"] = {"reused_from_member": reusable_member}
            anomaly = anomaly_kind

        return result, truth, None, anomaly, image_override

    if document_type == "PROVIDENT_FUND_STATEMENT":
        balance = salary * float(rng.uniform(2.0, 12.0))
        contributions = []
        for label in reversed(periods):
            employee = round(salary * 0.05, 2)
            employer_part = round(salary * 0.07, 2)
            balance += employee + employer_part
            contributions.append(
                {
                    "month": label,
                    "employee": _money(employee),
                    "employer": _money(employer_part),
                    "balance": _money(balance),
                }
            )

        context = {
            "fund_name": "National Provident Fund",
            "member_name": member["name_token"],
            "fund_number": f"PF-{member['member_id'][2:]}",
            "period": f"{periods[2]} to {periods[0]}",
            "employer_name": employer["name"],
            "contributions": contributions,
            "closing_balance": _money(balance),
        }
        result = renderer.render("provident_fund_statement.html.j2", context)
        truth = {
            k: context[k]
            for k in ("member_name", "fund_number", "period", "employer_name", "closing_balance")
        }
        return result, truth, None, None, None

    return None


def _sha256(data: bytes) -> str:
    from cio_common.hashing import sha256

    return sha256(data)


#: docs/07 §1.4 specifies a page phash with a Hamming threshold of 6. At the
#: default 64-bit size that does not work on documents: two identity cards for
#: different members sit a median of 2 bits apart, because the layout dominates
#: the pixels and the text is a rounding error. Measured over this corpus, a
#: 256-bit hash pushes the median apart by an order of magnitude while still
#: putting a genuine re-submission of the same page at distance 0.
PHASH_BITS = 16


def _phash(png: bytes) -> str:
    """Perceptual hash of the page, for the reused-image check in T-024."""
    import imagehash

    return str(imagehash.phash(Image.open(io.BytesIO(png)), hash_size=PHASH_BITS))


def _write(out: Path, corpus: Corpus) -> None:
    out.mkdir(parents=True, exist_ok=True)

    def dump(name: str, rows: list[dict[str, Any]]) -> None:
        (out / name).write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )

    dump("applications.jsonl", corpus.applications)
    dump("documents.jsonl", [d.as_row() for d in corpus.documents])
    dump("ground_truth.jsonl", [d.truth_row() for d in corpus.documents])
    (out / "anomalies.json").write_text(
        json.dumps([a.as_row() for a in corpus.anomalies], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
