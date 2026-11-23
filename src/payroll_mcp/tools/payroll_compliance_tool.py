"""Payroll compliance tool for verifying biweekly pay limits under 5 U.S.C. 5547."""

from __future__ import annotations
from typing import Annotated
from fastmcp import FastMCP
from pydantic import BaseModel, Field

# 2026 Reference Base GS Schedule (Step 1 Annual Base Pay before locality)
GS_BASE_STEP1: dict[int, float] = {
    1: 22360.00, 2: 25142.00, 3: 27434.00, 4: 30796.00, 5: 34454.00,
    6: 38407.00, 7: 42679.00, 8: 47266.00, 9: 52205.00, 10: 57489.00,
    11: 63163.00, 12: 75706.00, 13: 90025.00, 14: 106382.00, 15: 125133.00,
}

# 2026 OPM Locality Pay Percentages (Keyed by normalized lowercase name)
LOCALITY_RATES: dict[str, tuple[str, float]] = {
    "washington-dc": ("Washington-Baltimore-Arlington, DC-MD-VA-WV-PA", 0.3326),
    "dc": ("Washington-Baltimore-Arlington, DC-MD-VA-WV-PA", 0.3326),
    "new-york": ("New York-Newark, NY-NJ-CT-PA", 0.3721),
    "nyc": ("New York-Newark, NY-NJ-CT-PA", 0.3721),
    "san-francisco": ("San Jose-San Francisco-Oakland, CA", 0.4541),
    "philadelphia": ("Philadelphia-Reading-Camden, PA-NJ-DE-MD", 0.2869),
    "chicago": ("Chicago-Naperville, IL-IN-WI", 0.3046),
    "boston": ("Boston-Worcester-Providence, MA-RI-NH-ME", 0.3197),
    "los-angeles": ("Los Angeles-Long Beach, CA", 0.3584),
    "houston": ("Houston-The Woodlands, TX", 0.3472),
    "seattle": ("Seattle-Tacoma, WA", 0.3081),
    "denver": ("Denver-Aurora, CO", 0.2988),
    "atlanta": ("Atlanta--Athens-Clarke County--Sandy Springs, GA-AL", 0.2349),
    "dallas": ("Dallas-Fort Worth, TX-OK", 0.2695),
    "rest-of-us": ("Rest of United States (RUS)", 0.1682),
    "rus": ("Rest of United States (RUS)", 0.1682),
}

# Structured answer format (Pydantic Model) to break the payroll knowledge barrier
class PayrollComplianceResult(BaseModel):
    status: str = Field(description="APPROVED or EXCEEDS_CAP")
    locality_matched: str = Field(description="Official OPM locality pay area applied.")
    governing_limit_source: str = Field(description="Whether EX-V or GS-15/10 was the higher active biweekly cap.")
    biweekly_cap_amount: float = Field(description="The maximum allowed compensation for this pay period.")
    employee_base_biweekly: float = Field(description="The baseline biweekly salary including locality.")
    employee_premium_earned: float = Field(description="The total overtime or premium pay being evaluated.")
    employee_total_gross: float = Field(description="Base pay plus premium pay.")
    educational_note: str = Field(description="Clear explanation breaking down the confusion between biweekly and annual caps.")


def _calculate_annual_gs_pay(grade: int, step: int, locality_pct: float) -> float:
    """Computes annual GS salary with locality using OPM within-grade step increments."""
    step_1_base = GS_BASE_STEP1[grade]
    # OPM standard within-grade step increment is ~1/30th (3.333%) of Step 1 base per step
    within_grade_increment = round(step_1_base * 0.033333)
    raw_base = step_1_base + within_grade_increment * (step - 1)
    return round(raw_base * (1 + locality_pct), 2)


def register(mcp: FastMCP) -> None:
    """Register the payroll compliance tool with the MCP server."""
    
    @mcp.tool(
        name="validate_payroll_compliance",
        annotations={
            "title": "Verify Biweekly Premium Pay Cap Compliance",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def validate_payroll_compliance(
        grade: Annotated[int, Field(ge=1, le=15, description="The employee's GS grade (1 through 15)")],
        step: Annotated[int, Field(ge=1, le=10, description="The employee's GS step (1 through 10)")],
        locality: Annotated[str, Field(description="OPM locality area (e.g., 'Washington-DC', 'Philadelphia', 'New-York', 'San-Francisco', 'Rest-of-US')")],
        premium_pay_earned: Annotated[float, Field(ge=0.0, description="Total premium pay (overtime, night diff, Sunday premium) earned this pay period")]
    ) -> PayrollComplianceResult:
        """Validates whether a federal employee's biweekly compensation exceeds statutory limits under 5 U.S.C. 5547.
        
        Calculates the active 2026 ceiling (the greater of GS-15 Step 10 or Executive Schedule Level V)
        for the requested OPM locality pay area.
        """
        # Normalize user input (e.g., "New York" -> "new-york")
        normalized_loc = locality.strip().lower().replace(" ", "-")
        if normalized_loc not in LOCALITY_RATES:
            valid_options = ", ".join(sorted(k for k in LOCALITY_RATES if len(k) > 3))
            raise ValueError(
                f"Unknown locality '{locality}'. Supported localities: {valid_options}"
            )

        official_locality_name, locality_pct = LOCALITY_RATES[normalized_loc]

        # --- 2026 OPM Reference Figures ---
        ex_v_biweekly_cap = 7088.00  # Executive Schedule Level V biweekly limit
        
        # Dynamically compute GS-15 Step 10 biweekly cap for this specific locality
        gs15_10_annual = _calculate_annual_gs_pay(grade=15, step=10, locality_pct=locality_pct)
        gs15_10_biweekly = round((gs15_10_annual / 2087) * 80, 2)
        
        # Statutory Rule: The active cap is the GREATER of these two amounts
        active_biweekly_cap = max(ex_v_biweekly_cap, gs15_10_biweekly)
        governing_source = (
            "GS-15 Step 10 Locality Rate" if gs15_10_biweekly > ex_v_biweekly_cap else "Executive Schedule Level V"
        )

        # Calculate Employee Biweekly Base & Total Gross
        base_annual = _calculate_annual_gs_pay(grade=grade, step=step, locality_pct=locality_pct)
        base_biweekly = round((base_annual / 2087) * 80, 2)
        total_gross = round(base_biweekly + premium_pay_earned, 2)
        
        # Determine Compliance Status
        status = "APPROVED" if total_gross <= active_biweekly_cap else "EXCEEDS_CAP"
        
        educational_note = (
            "KNOWLEDGE BARRIER REMINDER: This check applies ONLY to the Biweekly Premium Pay Cap (5 U.S.C. 5547). "
            "INCLUDED in this biweekly calculation: Base Pay, Locality Pay, Overtime, Night/Sunday Premiums. "
            "EXCLUDED from this biweekly calculation: Recruitment/Retention incentives, performance awards, and travel reimbursements "
            "(those items count toward the ANNUAL Aggregate Compensation Cap instead under 5 U.S.C. 5307, which is a separate limit entirely)."
        )
        
        return PayrollComplianceResult(
            status=status,
            locality_matched=official_locality_name,
            governing_limit_source=governing_source,
            biweekly_cap_amount=active_biweekly_cap,
            employee_base_biweekly=base_biweekly,
            employee_premium_earned=premium_pay_earned,
            employee_total_gross=total_gross,
            educational_note=educational_note
        )
