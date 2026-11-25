"""Soundness test suite for the PCT proof engine.

Distinct from eval_three_system_physics.py. That harness scores the FINAL VALUE. This one
scores the DERIVATION: it catches the wrong-derivation-right-answer class of
bug, where the engine reaches a correct number by a route it had no business
taking.

Every case is specified as a goal state directly -- never as natural language --
so the engine is exercised in isolation from the stochastic parser.

For each case the suite asserts, as applicable:

  value          returned value matches expected within 1% relative tolerance
  rule set       the set of rules that fired equals the expected set exactly
  rule order     the ordered rule sequence, where a sequence was derived
  no re-derive   no rule in the derivation produces a quantity that was
                 SUPPLIED as a binding -- the manufactured-force check
  grounded       every binding step traces to a supplied input, and every
                 primitive step to a real KB primitive
  failure type   for cases expected to fail, the type equals the expected one

Ground truth (values, rule sequences) was derived by hand from kb1_physics.json's rule
definitions and from the physics -- never by running the engine and recording
whatever it happened to do.

Run:
    python tests/test_soundness.py

Exits nonzero if any case fails, so it can be used as a regression gate.
"""
from __future__ import annotations

import math
import os
import sys
import time

# Import the engine without depending on the caller's working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402

from pct.engine import load_kb, predicate_aliases, prove  # noqa: E402

KB = load_kb(DOMAINS["physics"]["kb1"])
RULES_BY_ID = {r["id"]: r for r in KB["rules"]}
RULE_OUTPUT = {r["id"]: r["output"]["predicate"] for r in KB["rules"]}
PRIMITIVE_IDS = {p["id"] for p in KB["primitives"]}

G = 9.8  # kb1_physics.json primitive grav_accel

# The biology domain, same engine. Cases carry `kb=KB_BIOLOGY` to select it;
# every case without that field keeps using the physics KB exactly as before.
KB_BIOLOGY = load_kb(DOMAINS["biology"]["kb1"])
KB_STOICH = load_kb(DOMAINS["chemistry"]["kb1"])


def _rule_output(kb: dict) -> dict:
    return {r["id"]: r["output"]["predicate"] for r in kb["rules"]}


def _primitive_ids(kb: dict) -> set:
    return {p["id"] for p in kb.get("primitives", [])}


# ---------------------------------------------------------------------------
# Case definitions. `bindings` uses a compact {name: (value, unit)} form.
# ---------------------------------------------------------------------------
def b(**kw):
    return {k: {"value": v[0], "unit": v[1]} for k, v in kw.items()}


CASES: list[dict] = []


def case(**kw):
    CASES.append(kw)


# --- correctness: shallow, exactly one rule ---------------------------------

case(
    name="F=ma (single rule)",
    why=("newton_second_law is the only net_force rule in context dynamics (m a is the "
         "NET force; output renamed force -> net_force 2026-11-22)."),
    goal={"predicate": "net_force", "unit": "N", "context": "dynamics"},
    bindings=b(mass=(3.0, "kg"), acceleration=(4.0, "m/s2")),
    expect_value=3.0 * 4.0,
    expect_rules={"newton_second_law"},
    expect_order=["newton_second_law"],
)

case(
    name="Kinetic energy from mass and velocity (single rule)",
    why="kinetic_energy = 0.5*m*v^2; both inputs supplied, nothing to derive.",
    goal={"predicate": "kinetic_energy", "unit": "J", "context": "energy"},
    bindings=b(mass=(2.0, "kg"), velocity=(3.0, "m/s")),
    expect_value=0.5 * 2.0 * 3.0 ** 2,
    expect_rules={"kinetic_energy"},
    expect_order=["kinetic_energy"],
)

case(
    name="Momentum from mass and velocity (single rule)",
    why="momentum = m*v in context dynamics.",
    goal={"predicate": "momentum", "unit": "kg*m/s", "context": "dynamics"},
    bindings=b(mass=(2.5, "kg"), velocity=(4.0, "m/s")),
    expect_value=2.5 * 4.0,
    expect_rules={"momentum"},
    expect_order=["momentum"],
)

case(
    name="Work from force, displacement, angle (single rule)",
    why="work_from_force_displacement = F*d*cos(theta); all three supplied.",
    goal={"predicate": "work", "unit": "J", "context": "energy"},
    bindings=b(force=(20.0, "N"), displacement=(5.0, "m"),
               force_displacement_angle=(60.0, "deg")),  # was `angle` (split 2026-11-21)
    expect_value=20.0 * 5.0 * math.cos(math.radians(60.0)),
    expect_rules={"work_from_force_displacement"},
    expect_order=["work_from_force_displacement"],
)

case(
    name="Pressure must compute F/A, not short-circuit to the atm primitive",
    why=(
        "`pressure` is BOTH a rule output and the standard_atm_pressure primitive. "
        "The depth>0 guard must stop a top-level goal terminating on the primitive."
    ),
    goal={"predicate": "pressure", "unit": "Pa", "context": "mechanics"},
    bindings=b(force=(50.0, "N"), area=(5.0, "m2")),
    expect_value=50.0 / 5.0,
    expect_rules={"pressure_from_force"},
    expect_order=["pressure_from_force"],
    forbid_primitive_ids={"standard_atm_pressure"},
)

# --- correctness: multi-step ------------------------------------------------

case(
    name="Free-fall velocity (single rule, exact context)",
    why=("free_fall_velocity = g*t, reachable directly when the goal names free_fall. "
         "Since the 2026-11-21 audit the release-from-rest condition is an input guarded "
         "v0 == 0, so the case states it (initial_velocity = 0)."),
    goal={"predicate": "velocity", "unit": "m/s", "context": "free_fall"},
    bindings=b(time=(2.0, "s"), initial_velocity=(0.0, "m/s")),
    expect_value=G * 2.0,
    expect_rules={"free_fall_velocity"},
    expect_order=["free_fall_velocity"],
)

case(
    name="Kinetic energy of an object falling from rest for t seconds: F5 (KB gap)",
    why=(
        "Updated 2026-11-25 (paper inputs, item 6). The query now states the start "
        "(from rest: initial_velocity = 0), as AUDIT A requires of free_fall_velocity. "
        "The physical answer is v = g t, KE = 0.5*4*(9.8*2)^2 = 768.32 J, but the KB has "
        "no context transition from `energy` to `free_fall`: the engine reaches only "
        "kinematics, whose velocity rules need an acceleration, and acceleration needs a "
        "net force (a cycle). Expected: the F5 certificate, naming net_force. No answer "
        "through weight_from_mass (weight is not a net force) -- the non-canonical route "
        "that recovers g as weight / mass must not occur. (Test-set q152, q153: same gap.)"
    ),
    goal={"predicate": "kinetic_energy", "unit": "J", "context": "energy"},
    bindings=b(mass=(4.0, "kg"), time=(2.0, "s"), initial_velocity=(0.0, "m/s")),
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="net_force",
)

case(
    name="Mechanical energy from mass, velocity, height (3 rules)",
    why=(
        "mechanical_energy = KE + PE. KE from kinetic_energy (0.5*5*3^2 = 22.5), "
        "PE from gravitational_potential_energy (5*9.8*10 = 490). Total 512.5 J."
    ),
    goal={"predicate": "mechanical_energy", "unit": "J", "context": "energy"},
    bindings=b(mass=(5.0, "kg"), velocity=(3.0, "m/s"), height=(10.0, "m")),
    expect_value=0.5 * 5.0 * 3.0 ** 2 + 5.0 * G * 10.0,
    expect_rules={"kinetic_energy", "gravitational_potential_energy", "mechanical_energy"},
    expect_order=["kinetic_energy", "gravitational_potential_energy", "mechanical_energy"],
)

# --- regression guard: Bug 1, the net-force / manufactured-force leak -------

case(
    name="REGRESSION Bug 1 -- net_force must not become m*g (the 385.14 case)",
    why=(
        "acceleration_from_net_force is the only acceleration rule in "
        "newtonian_motion. It must consume the SUPPLIED net_force (83.2/39.3 = "
        "2.117), never manufacture a force via weight_from_mass (39.3*9.8 = 385.14)."
    ),
    goal={"predicate": "acceleration", "unit": "m/s2", "context": "newtonian_motion"},
    bindings=b(net_force=(83.2, "N"), mass=(39.3, "kg")),
    expect_value=83.2 / 39.3,
    expect_rules={"acceleration_from_net_force"},
    expect_order=["acceleration_from_net_force"],
    forbid_rules={"weight_from_mass", "newton_second_law"},
    forbid_derived_predicates={"force", "net_force"},
)

case(
    name="REGRESSION Bug 1 -- same leak at the end of a longer chain",
    why=(
        "displacement = 0.5*a*t^2 with a = F_net/m. net_force is supplied, so the "
        "force must come from the binding, not weight_from_mass. "
        "a = 182.2/24.3 = 7.4979; s = 0.5*7.4979*2.2^2 = 18.145 m. The start from "
        "rest is stated as initial_velocity = 0 (the from-rest rule was removed)."
    ),
    goal={"predicate": "displacement", "unit": "m", "context": "kinematics"},
    bindings=b(net_force=(182.2, "N"), mass=(24.3, "kg"), time=(2.2, "s"),
               initial_velocity=(0.0, "m/s")),
    expect_value=0.5 * (182.2 / 24.3) * 2.2 ** 2,
    forbid_rules={"weight_from_mass"},
    forbid_derived_predicates={"net_force"},
)

# --- regression guard: supplied quantities must never be re-derived ---------

case(
    name="Supplied velocity must be used, not derived",
    why=(
        "mass and time would let the engine derive a velocity, but velocity is "
        "SUPPLIED as 10 m/s. KE must be 0.5*4*100 = 200 J via one rule."
    ),
    goal={"predicate": "kinetic_energy", "unit": "J", "context": "energy"},
    bindings=b(mass=(4.0, "kg"), time=(2.0, "s"), velocity=(10.0, "m/s")),
    expect_value=0.5 * 4.0 * 10.0 ** 2,
    expect_rules={"kinetic_energy"},
    forbid_derived_predicates={"velocity"},
)

case(
    name="Supplied KE and PE must be used, not derived",
    why=(
        "mass/velocity/height are also present and would let both components be "
        "derived, but both are supplied. Answer must be 200+150 = 350 J, one rule."
    ),
    goal={"predicate": "mechanical_energy", "unit": "J", "context": "energy"},
    bindings=b(
        kinetic_energy=(200.0, "J"), gravitational_potential_energy=(150.0, "J"),
        mass=(5.0, "kg"), velocity=(3.0, "m/s"), height=(10.0, "m"),
    ),
    expect_value=350.0,
    expect_rules={"mechanical_energy"},
    forbid_rules={"kinetic_energy", "gravitational_potential_energy"},
    forbid_derived_predicates={"kinetic_energy", "gravitational_potential_energy"},
)

# --- regression guard: Bug 3, the cycle guard -------------------------------

case(
    name="REGRESSION Bug 3 -- velocity/period/angular_velocity cycle terminates",
    why=(
        "With radius bound, no leaf exhausts: velocity_from_angular needs "
        "angular_velocity, which needs period, which needs velocity. A true cycle. "
        "Must be cut structurally, not by burning d_max=200."
    ),
    goal={"predicate": "velocity", "unit": "m/s", "context": "circular_motion"},
    bindings=b(radius=(2.0, "m")),
    d_max=200,
    expect_status="failure",
    # F5 family. The F5a/F5b distinction is a per-QUERY property decided by
    # re-running the gold parse (see reconcile_f5.py); a suite case IS a gold
    # goal state, so the distinction does not apply to it.
    expect_type="F5",
    expect_cycle=True,
    max_seconds=1.0,
)

case(
    name="REGRESSION Bug 3 -- force/acceleration cycle terminates at high d_max",
    why=(
        "acceleration_from_force needs force; newton_second_law needs acceleration. "
        "With nothing bound this must fail fast rather than recursing to d_max=200."
    ),
    goal={"predicate": "acceleration", "unit": "m/s2", "context": "dynamics"},
    bindings={},
    d_max=200,
    expect_status="failure",
    max_seconds=1.0,
)

# --- regression guard: leaf exhaustion --------------------------------------

case(
    name="Leaf exhaustion -> F5 (angle is the output of no rule)",
    why=(
        "work_from_force_displacement needs an angle. `force_displacement_angle` is input-only in "
        "kb1_physics.json -- no rule outputs it and it is not a primitive -- so the engine "
        "must return the F5 certificate, not a depth cutoff."
    ),
    goal={"predicate": "work", "unit": "J", "context": "energy"},
    bindings=b(force=(20.0, "N"), displacement=(5.0, "m")),
    expect_status="failure",
    # F5 family: `angle` is the output of no rule in kb1_physics.json.
    expect_type="F5",
)

# --- regression guard: Bug 2, failure-type precedence -----------------------

case(
    name="REGRESSION Bug 2 -- mixed {F1, F5} candidate set reports the F5",
    why=(
        "velocity@kinematics has three candidate rules. With only `time` bound and "
        "d_max=2: kinematic_velocity_from_rest bottoms out at the depth cap (F1), "
        "while the two _with_initial/_from_displacement rules leaf-exhaust on "
        "initial_velocity (F5). The certificate must win."
    ),
    goal={"predicate": "velocity", "unit": "m/s", "context": "kinematics"},
    bindings=b(time=(2.0, "s")),
    d_max=2,
    expect_status="failure",
    # F5 family: the certificate comes from `initial_velocity`, which no rule
    # in kb1_physics.json produces.
    expect_type="F5",
)

case(
    name="REGRESSION Bug 2 -- discriminating case (F1 strictly last)",
    why=(
        "Here the F1 candidate is tried LAST, so the pre-fix `last_failure` rule "
        "would have reported F1 and the precedence rule reports F5. NOTE: this "
        "needs a context=None goal, which the parser never emits -- it is an "
        "API-level case, not a production-shaped one. See the report note."
    ),
    goal={"predicate": "acceleration", "unit": "m/s2", "context": None},
    bindings=b(angular_velocity=(2.0, "rad/s"), net_force=(3.0, "N")),
    d_max=2,
    expect_status="failure",
    # F5 family: acceleration_from_force has its net_force bound but needs `mass`,
    # which no rule in kb1_physics.json produces.
    expect_type="F5",
)

# --- context-discipline guards ---------------------------------------------

case(
    name="CONTEXT whitelisted transition energy -> velocity -> kinematics resolves",
    why=(
        "kinetic_energy@energy needs a velocity; no velocity rule lives in `energy`. "
        "The whitelisted (energy, velocity, kinematics) transition must let "
        "kinematic_velocity_with_initial fire: v = 0 + 3*2 = 6, KE = 0.5*4*36 = 72 J. "
        "(Was kinematic_velocity_from_rest, removed 2026-11-21; v0 = 0 is now stated.)"
    ),
    goal={"predicate": "kinetic_energy", "unit": "J", "context": "energy"},
    bindings=b(mass=(4.0, "kg"), acceleration=(3.0, "m/s2"), time=(2.0, "s"),
               initial_velocity=(0.0, "m/s")),
    expect_value=0.5 * 4.0 * (3.0 * 2.0) ** 2,
    expect_rules={"kinematic_velocity_with_initial", "kinetic_energy"},
    expect_order=["kinematic_velocity_with_initial", "kinetic_energy"],
    forbid_rules={"weight_from_mass"},
)

case(
    name="CONTEXT excluded transition energy -> force -> dynamics refuses",
    why=(
        "Pre-fix this leaked: work@energy needed a force, found no force rule in "
        "`energy`, and drifted to weight_from_mass, answering 84.87 J from an "
        "invented force. (energy, force, dynamics) is deliberately NOT whitelisted, "
        "so the engine must refuse."
    ),
    goal={"predicate": "work", "unit": "J", "context": "energy"},
    bindings=b(mass=(10.0, "kg"), displacement=(2.0, "m"),
               force_displacement_angle=(30.0, "deg")),
    expect_status="failure",
    expect_type="F3",
    forbid_rules={"weight_from_mass", "newton_second_law"},
)


# --- 2026-11-21 rule audit: each fix pinned ------------------------------------
GRAV_G = next(p["value"] for p in KB["primitives"] if p["predicate"] == "gravitational_constant")

case(
    name="AUDIT A  v0 bound: kinematics uses the general rule",
    why=("An object moving at 12.2 m/s accelerates at 0.8 m/s^2 for 1.4 s: v = v0 + a t "
         "= 13.32. Before the audit kinematic_velocity_from_rest fired first and "
         "returned a t = 1.12, ignoring the bound v0."),
    goal={"predicate": "velocity", "unit": "m/s", "context": "kinematics"},
    bindings=b(initial_velocity=(12.2, "m/s"), acceleration=(0.8, "m/s2"), time=(1.4, "s")),
    expect_value=12.2 + 0.8 * 1.4,
    expect_rules={"kinematic_velocity_with_initial"},
    expect_binding_predicates={"initial_velocity"},
)

case(
    name="AUDIT A  from rest stated (v0 = 0): the general rule gives the from-rest value",
    why="v0 = 0 bound: v = 0 + a t = 13.0 * 0.9 = 11.7, the value the removed rule gave.",
    goal={"predicate": "velocity", "unit": "m/s", "context": "kinematics"},
    bindings=b(initial_velocity=(0.0, "m/s"), acceleration=(13.0, "m/s2"), time=(0.9, "s")),
    expect_value=13.0 * 0.9,
    expect_rules={"kinematic_velocity_with_initial"},
)

case(
    name="AUDIT A  from rest stated: displacement = a t^2 / 2 via the general rule",
    why="v0 = 0 bound: x = 0*t + 0.5 a t^2 = 0.5 * 12.2 * 1.1^2 = 7.381.",
    goal={"predicate": "displacement", "unit": "m", "context": "kinematics"},
    bindings=b(initial_velocity=(0.0, "m/s"), acceleration=(12.2, "m/s2"), time=(1.1, "s")),
    expect_value=0.5 * 12.2 * 1.1 ** 2,
    expect_rules={"kinematic_displacement_with_initial"},
)

case(
    name="AUDIT A  v0 unbound and unstated: F5 naming initial_velocity",
    why=("No starting velocity is stated, so no kinematics rule applies: the result is "
         "the F5 certificate naming initial_velocity, never a t."),
    goal={"predicate": "velocity", "unit": "m/s", "context": "kinematics"},
    bindings=b(acceleration=(3.0, "m/s2"), time=(2.0, "s")),
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="initial_velocity",
)

case(
    name="AUDIT A  free fall with v0 unstated: F5 naming initial_velocity",
    why="'falling for 3.8 s' states no start: free_fall_velocity's v0 input is unbound.",
    goal={"predicate": "velocity", "unit": "m/s", "context": "free_fall"},
    bindings=b(time=(3.8, "s")),
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="initial_velocity",
)

case(
    name="AUDIT A  free fall with a nonzero v0: guard fails (F6), no g t answer",
    why="v = g t is false for a thrown object; with v0 = 5 bound the guard v0 == 0 fails.",
    goal={"predicate": "velocity", "unit": "m/s", "context": "free_fall"},
    bindings=b(time=(2.0, "s"), initial_velocity=(5.0, "m/s")),
    expect_status="failure", expect_type="F6",
)

case(
    name="AUDIT A  torque at 30 degrees uses sin(theta)",
    why=("tau = F r sin(30) = 113.2*1.65*0.5 = 93.39. Before the audit "
         "torque_from_force fired first and returned F r = 186.78."),
    goal={"predicate": "torque", "unit": "N*m", "context": "rotational_motion"},
    bindings=b(force=(113.2, "N"), radius=(1.65, "m"), torque_angle=(30.0, "deg")),
    expect_value=113.2 * 1.65 * math.sin(math.radians(30.0)),
    expect_rules={"torque_with_angle"},
)

case(
    name="AUDIT A  perpendicular torque (torque_angle = 90) gives F r",
    why="sin(90) = 1: tau = 109.8 * 2.58 = 283.284, the value the removed rule gave.",
    goal={"predicate": "torque", "unit": "N*m", "context": "rotational_motion"},
    bindings=b(force=(109.8, "N"), radius=(2.58, "m"), torque_angle=(90.0, "deg")),
    expect_value=109.8 * 2.58,
    expect_rules={"torque_with_angle"},
)

case(
    name="AUDIT A  normal force on a stated flat surface (incline_angle = 0)",
    why="N = m g = 11.0 * 9.8; the flat-surface condition is a bound value, guard theta == 0.",
    goal={"predicate": "normal_force", "unit": "N", "context": "newtonian_motion"},
    bindings=b(mass=(11.0, "kg"), incline_angle=(0.0, "deg")),
    expect_value=11.0 * G,
    expect_rules={"normal_force_flat"},
)

case(
    name="AUDIT A  hanging-mass tension needs the stated rest (acceleration = 0)",
    why="T = m g only if a = 0; with a = 0 bound, T = 49.8 * 9.8.",
    goal={"predicate": "tension", "unit": "N", "context": "newtonian_motion"},
    bindings=b(mass=(49.8, "kg"), acceleration=(0.0, "m/s2")),
    expect_value=49.8 * G,
    expect_rules={"tension_hanging_mass"},
)

case(
    name="AUDIT A  pendulum period with small amplitude stated",
    why="T = 2 pi sqrt(L/g) is the small-amplitude result; pendulum_amplitude = 'small' bound.",
    goal={"predicate": "period", "unit": "s", "context": "simple_harmonic_motion"},
    bindings={"length": {"value": 0.74, "unit": "m"},
              "pendulum_amplitude": {"value": "small", "unit": "condition"}},
    expect_value=2 * math.pi * math.sqrt(0.74 / G),
    expect_rules={"period_pendulum"},
)

case(
    name="F5 DIAGNOSTIC  pendulum, amplitude unstated: every failed candidate is listed",
    why=("'period of a 0.74 m pendulum' with no amplitude stated. Two candidate rules "
         "produce period in simple_harmonic_motion and both fail with F5: the "
         "certificate must name period_spring_mass: mass AND period_pendulum: "
         "pendulum_amplitude, not only the first (pre-freeze Part C). The reported "
         "type and the first-candidate goal are unchanged."),
    goal={"predicate": "period", "unit": "s", "context": "simple_harmonic_motion"},
    bindings=b(length=(0.74, "m")),
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="mass",
    expect_f5_candidates={("period_spring_mass", "mass"),
                          ("period_pendulum", "pendulum_amplitude")},
)

# --- pre-freeze predicate audit (2026-11-22): one predicate, one quantity -----

case(
    name="PREDICATE  weight is not the net force: a = F_net/m cannot use m g",
    why=("Pre-audit, acceleration_from_force read `force`, and dynamics -> force -> "
         "gravity let weight_from_mass supply it: a = m g / m = g for any mass. It now "
         "reads net_force; with only the mass bound the engine must fail."),
    goal={"predicate": "acceleration", "unit": "m/s2", "context": "dynamics"},
    bindings=b(mass=(5.0, "kg")),
    expect_status="failure", expect_type="F5",
    forbid_rules={"weight_from_mass"},
)

case(
    name="PREDICATE  a force not stated as net does not give a = F/m",
    why=("'a force of 138.6 N acts on a 46.3 kg object' binds force; a = F/m needs the "
         "net force, which the query does not state."),
    goal={"predicate": "acceleration", "unit": "m/s2", "context": "dynamics"},
    bindings=b(mass=(46.3, "kg"), force=(138.6, "N")),
    expect_status="failure", expect_type="F5",
)

case(
    name="PREDICATE  a net force fills an input that accepts any force (alias kept)",
    why=("force: [net_force] runs specific -> general: the impulse of the stated net "
         "force is J = F t = 50 * 3."),
    goal={"predicate": "impulse", "unit": "N*s", "context": "dynamics"},
    bindings=b(net_force=(50.0, "N"), time=(3.0, "s")),
    expect_value=150.0,
    expect_rules={"impulse"},
)

case(
    name="PREDICATE  maximum static friction is not the friction force",
    why=("static_friction_max outputs max_static_friction (mu_s N, an upper bound), so "
         "it cannot supply friction_force to the net-force rule."),
    goal={"predicate": "net_force", "unit": "N", "context": "newtonian_motion"},
    bindings=b(applied_force=(40.0, "N"), applied_force_angle=(0.0, "deg"),
               static_friction_coefficient=(0.3, "dimensionless"),
               normal_force=(50.0, "N")),
    expect_status="failure", expect_type="F5",
    forbid_rules={"static_friction_max"},
)

case(
    name="PREDICATE  a displacement of motion is not the gravitational separation",
    why=("gravitational_force reads `separation`; the displacement<->distance aliases "
         "were removed, so a displacement cannot stand in for r."),
    goal={"predicate": "force", "unit": "N", "context": "gravitation"},
    bindings=b(mass_1=(100.0, "kg"), mass_2=(200.0, "kg"), displacement=(4.0, "m")),
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="separation",
)

case(
    name="PREDICATE  mechanical energy does not add rotational kinetic energy",
    why=("mechanical_energy = (1/2) m v^2 + m g h. Rotational KE ((1/2) I w^2) is now its "
         "own predicate and cannot fill the kinetic_energy input."),
    goal={"predicate": "mechanical_energy", "unit": "J", "context": "energy"},
    bindings=b(moment_of_inertia=(2.0, "kg*m2"), angular_velocity=(3.0, "rad/s"),
               mass=(1.0, "kg"), height=(2.0, "m")),
    expect_status="failure", expect_type="F5",
    forbid_rules={"rotational_kinetic_energy"},
)

case(
    name="PREDICATE  impulse-momentum theorem from a stated net force",
    why=("v = v0 + J_net/m with J_net = F_net t = 20 * 2 = 40 N s, m = 8 kg, v0 = 1 m/s "
         "-> 6 m/s. The new net_impulse rule feeds the theorem."),
    goal={"predicate": "velocity", "unit": "m/s", "context": "newtonian_motion"},
    bindings=b(net_force=(20.0, "N"), time=(2.0, "s"), mass=(8.0, "kg"),
               initial_velocity=(1.0, "m/s")),
    expect_value=6.0,
    expect_rules={"net_impulse", "impulse_momentum_theorem"},
)

case(
    name="AUDIT A  energy conservation needs nonconservative_work = 'none' stated",
    why="Without the stated condition the conservation rule cannot fire: F5 naming it.",
    goal={"predicate": "velocity", "unit": "m/s", "context": "energy_conservation"},
    bindings=b(initial_velocity=(5.5, "m/s"), initial_height=(15.7, "m"), height=(0.2, "m")),
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="nonconservative_work",
)

case(
    name="AUDIT B  gravitational force uses BOTH distinct masses",
    why=("F = G m1 m2 / r^2 = G * 510017 * 91819 / 57^2. Before the audit both inputs "
         "had predicate `mass` and resolved from ONE binding (m1 = m2)."),
    goal={"predicate": "force", "unit": "N", "context": "gravitation"},
    bindings=b(mass_1=(510017.0, "kg"), mass_2=(91819.0, "kg"), separation=(57.0, "m")),
    expect_value=GRAV_G * 510017.0 * 91819.0 / 57.0 ** 2,
    expect_rules={"gravitational_force"},
    expect_binding_predicates={"mass_1", "mass_2"},
)

case(
    name="AUDIT B  elastic collision uses both masses and both velocities",
    why=("v1f = ((m1-m2) v1i + 2 m2 v2i)/(m1+m2) with m1=3.9, m2=7.0, v1i=10.3, "
         "v2i=-4.7 = -8.966. Pre-audit the collapsed bindings returned -4.7."),
    goal={"predicate": "velocity", "unit": "m/s", "context": "elastic_collision"},
    bindings={"mass_1": {"value": 3.9, "unit": "kg"}, "mass_2": {"value": 7.0, "unit": "kg"},
              "initial_velocity_1": {"value": 10.3, "unit": "m/s"},
              "initial_velocity_2": {"value": -4.7, "unit": "m/s"},
              "collision_type": {"value": "elastic", "unit": "condition"}},
    expect_value=((3.9 - 7.0) * 10.3 + 2 * 7.0 * -4.7) / (3.9 + 7.0),
    expect_rules={"elastic_collision_v1"},
    expect_binding_predicates={"mass_1", "mass_2", "initial_velocity_1",
                               "initial_velocity_2"},
)

case(
    name="AUDIT A  collision type unstated: F5 naming collision_type",
    why="'inelastic' alone or no type at all: the elastic formula must not fire.",
    goal={"predicate": "velocity", "unit": "m/s", "context": "elastic_collision"},
    bindings=b(mass_1=(3.9, "kg"), mass_2=(7.0, "kg"), initial_velocity_1=(10.3, "m/s"),
               initial_velocity_2=(-4.7, "m/s")),
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="collision_type",
)

case(
    name="AUDIT C  goal state binding its own target is a parse-stage F4",
    why=("'acceleration produced when a 280.7 N force acts on a 34.4 kg mass' parsed as "
         "goal force with force = 280.7 bound (stored q25): the engine would echo 280.7. "
         "Since 2026-11-22 the F = m a goal is net_force."),
    stage="parser",
    goal={"predicate": "net_force", "unit": "N", "context": "dynamics"},
    bindings=b(mass=(34.4, "kg"), net_force=(280.7, "N")),
    expect_status="failure",
    expect_reason="goal state binds the quantity it asks for",
)

case(
    name="AUDIT C  ...also when the bound name is an alias the engine accepts",
    why=("Stored q84: goal force with net_force bound; kb1 lets a net_force binding stand "
         "in for force (force: [net_force]), so the engine would return the bound 83.2. "
         "Context gravity: since 2026-11-22 force is not a dynamics goal."),
    stage="parser",
    goal={"predicate": "force", "unit": "N", "context": "gravity"},
    bindings=b(mass=(39.3, "kg"), net_force=(83.2, "N")),
    expect_status="failure",
    expect_reason="goal state binds the quantity it asks for",
)

case(
    name="AUDIT C  a non-trivial goal state passes parser validation",
    why="Control: goal acceleration with force and mass bound is a real question.",
    stage="parser",
    goal={"predicate": "acceleration", "unit": "m/s2", "context": "dynamics"},
    bindings=b(mass=(34.4, "kg"), force=(280.7, "N")),
    expect_status="success",
)


# ===========================================================================
# BIOLOGY DOMAIN -- ONE KB (kb1_biology.json): trait-based vertebrate
# classification and envelope-based felid (deep/cat) classification, in one
# taxonomy of 52 taxa driven by 11 UNIVERSAL level rules.
#
# Two discriminator kinds, both preserved exactly as the source KBs behaved:
#   TRAITS    required traits are rule INPUTS, so an absent trait is an
#             unsatisfiable subgoal -> F5 ("something is missing")
#   ENVELOPE  characters are inputs and the range/membership test is in the
#             GUARD, so a non-match with everything bound -> F6 ("nothing
#             missing, the facts do not match")
#
# Goals are now LEVEL predicates and chains name the universal rules; every
# asserted classification and failure type is unchanged from the split KBs.
# Sources: domains/biology/source_vertebrates.json (definitional proofs for the vertebrate taxa,
# OpenStax Biology 2e Ch 29 / Campbell Ch 34); domains/biology/source_felidae.json (Kitchener
# et al. 2017; "List of felids" per-species IUCN assessments) for the felid sub-tree.
# ===========================================================================

BIOLOGY = {"kb": KB_BIOLOGY, "d_max": 12}
_BT = KB_BIOLOGY["taxa"]
_MAXT = 2


def _bio_anc(taxon):
    chain = [taxon]
    while _BT[chain[-1]]["parent"] is not None:
        chain.append(_BT[chain[-1]]["parent"])
    return list(reversed(chain))


def bio(taxon, traits=None, chars=None, stop_at_entry=True):
    """Goal + bindings for one candidate taxon, resolving its ancestor chain out
    of the KB `taxa` data exactly as tools/eval/eval_biology.py does."""
    traits = traits or {}
    chars = chars or {}
    bnd = {}
    if chars:
        bnd["is_felid"] = {"value": True, "unit": "trait"}
        for k, u in (("body_length", "cm"), ("geographic_range", "region"),
                     ("habitat", "habitat")):
            if k in chars:
                bnd[k] = {"value": chars[k], "unit": u}
    chain = _bio_anc(taxon)
    if stop_at_entry and not traits and "felidae" in chain:
        chain = chain[chain.index("felidae"):]
    for label in chain:
        t = _BT[label]
        n = t["level"]
        bnd[f"candidate_L{n}"] = {"value": label, "unit": "label"}
        if t["discriminator"] == "traits":
            req = t["required_traits"]
            for k in range(1, _MAXT + 1):
                if k <= len(req):
                    if req[k - 1] in traits:      # absent -> UNBOUND -> F5
                        bnd[f"trait_{k}_L{n}"] = {"value": True, "unit": "trait"}
                else:
                    bnd[f"trait_{k}_L{n}"] = {"value": True, "unit": "trait"}
        elif t["discriminator"] == "envelope":
            bnd[f"body_min_L{n}"] = {"value": t["body_min"], "unit": "cm"}
            bnd[f"body_max_L{n}"] = {"value": t["body_max"], "unit": "cm"}
            bnd[f"regions_L{n}"] = {"value": t["regions"], "unit": "regionset"}
            bnd[f"habitats_L{n}"] = {"value": t["habitats"], "unit": "habitatset"}
    goal = {"predicate": f"taxon_{_BT[taxon]['level']}", "unit": "taxon",
            "context": "biology_taxonomy"}
    return goal, bnd


def _trait_chain(taxon):
    """Universal rule ids for the trait-side chain up to `taxon`."""
    ids = ["taxon_L1_root"]
    for lvl in range(2, _BT[taxon]["level"] + 1):
        ids.append(f"taxon_L{lvl}_from_L{lvl-1}_by_traits")
    return ids


def _felid_chain(taxon):
    """Universal rule ids for a felid-only chain (entry at L6) up to `taxon`."""
    ids = ["taxon_L6_from_specimen"]
    for lvl in range(7, _BT[taxon]["level"] + 1):
        ids.append(f"taxon_L{lvl}_from_L{lvl-1}_by_envelope")
    return ids


def _T(*names):
    return {n: True for n in names}


# --- vertebrate side: trait presence, F5 on an absent trait -----------------

_g, _b = bio("monotreme", traits=_T("has_backbone", "amniotic_egg",
                                    "produces_milk", "has_hair", "lays_eggs"))
case(
    name="BIOLOGY platypus is a monotreme (4-rule trait chain)",
    why=("Platypus has backbone, amniotic egg, milk, hair, and lays eggs. Taxonomy: "
         "vertebrate -> amniote -> mammal -> monotreme. Monotremes are the egg-laying "
         "mammals, so milk and hair must still be satisfied on the way."),
    goal=_g, bindings=_b,
    expect_value="monotreme",
    expect_rules=set(_trait_chain("monotreme")),
    expect_order=_trait_chain("monotreme"),
    **BIOLOGY,
)

_g, _b = bio("cetacean", traits=_T("has_backbone", "amniotic_egg", "produces_milk",
                                   "has_hair", "live_birth", "has_placenta",
                                   "has_flippers", "has_blowhole"))
case(
    name="BIOLOGY dolphin is a cetacean (5-rule trait chain, deepest vertebrate)",
    why=("vertebrate -> amniote -> mammal -> placental -> cetacean. A dolphin is a "
         "placental mammal with flippers and a blowhole; it must not resolve as a fish."),
    goal=_g, bindings=_b,
    expect_value="cetacean",
    expect_rules=set(_trait_chain("cetacean")),
    expect_order=_trait_chain("cetacean"),
    **BIOLOGY,
)

_g, _b = bio("marsupial", traits=_T("has_backbone", "amniotic_egg", "produces_milk",
                                    "has_hair", "has_pouch", "live_birth"))
case(
    name="BIOLOGY kangaroo is a marsupial",
    why="vertebrate -> amniote -> mammal -> marsupial, on the pouch.",
    goal=_g, bindings=_b,
    expect_value="marsupial",
    expect_order=_trait_chain("marsupial"),
    **BIOLOGY,
)

_g, _b = bio("placental", traits=_T("has_backbone", "amniotic_egg", "produces_milk",
                                    "has_hair", "has_pouch", "live_birth"))
case(
    name="BIOLOGY REFUSAL placental underivable for kangaroo (F5: has_placenta not supplied)",
    why=("A kangaroo's stated traits include live birth but not has_placenta. That "
         "trait is a rule INPUT, so the placental subgoal cannot be satisfied and the "
         "engine must return F5 -- something is missing -- not F6. This certifies that "
         "placental membership is underivable, NOT that a kangaroo is not placental: "
         "the KB declares no disjointness between sibling taxa. The kangaroo's "
         "derivable classification, marsupial, is asserted by its own case."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F5",
    **BIOLOGY,
)

_g, _b = bio("bird", traits=_T("has_backbone", "amniotic_egg", "has_feathers",
                               "live_birth", "has_placenta"))
case(
    name="BIOLOGY REFUSAL bird underivable for the chimeric specimen (F5)",
    why=("Feathers plus live birth plus a placenta. bird needs lays_eggs, which is "
         "not stated, so the bird trait subgoal is unsatisfiable: F5. This does not "
         "make the specimen classless: backbone and amniotic egg ARE stated, so "
         "amniote is derivable -- see the next case."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F5",
    **BIOLOGY,
)

_g, _b = bio("amniote", traits=_T("has_backbone", "amniotic_egg", "has_feathers",
                                  "live_birth", "has_placenta"))
case(
    name="BIOLOGY chimeric specimen classifies as amniote (the answer in place of 'no')",
    why=("Same chimeric specimen. vertebrate needs has_backbone and amniote needs "
         "amniotic_egg; both are stated, so the two-rule chain completes. None of "
         "bird (lays_eggs), mammal (produces_milk, has_hair) or reptile "
         "(has_keratin_scales, ectothermic) has its traits stated, so amniote is the "
         "deepest derivable taxon -- the 'derived: amniote' a driver reports when "
         "asked whether this specimen is a bird."),
    goal=_g, bindings=_b,
    expect_value="amniote",
    expect_rules=set(_trait_chain("amniote")),
    expect_order=_trait_chain("amniote"),
    **BIOLOGY,
)

_g, _b = bio("fish", traits=_T("has_gills", "has_fins"))
case(
    name="BIOLOGY REFUSAL no taxon derivable without a backbone (F5)",
    why=("Gills and fins but no backbone stated. fish requires vertebrate, which "
         "requires has_backbone; unsupplied and produced by no rule, so the chain is "
         "refused with the F5 certificate. Every taxon in the KB descends from "
         "vertebrate, so no classification is derivable either: the drivers answer "
         "with the typed failure alone."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F5",
    **BIOLOGY,
)

_g, _b = bio("bird", traits=_T("has_backbone", "amniotic_egg", "has_feathers",
                               "lays_eggs"))
case(
    name="BIOLOGY eagle is a bird, and no mammal rule leaks in",
    why=("vertebrate -> amniote -> bird on feathers + egg-laying. Feathers are the "
         "defining synapomorphy of Aves; mammalian traits are absent so the mammal "
         "branch cannot be taken."),
    goal=_g, bindings=_b,
    expect_value="bird",
    expect_order=_trait_chain("bird"),
    **BIOLOGY,
)

_g, _b = bio("fish", traits=_T("has_backbone", "has_gills", "has_fins", "lays_eggs"))
case(
    name="BIOLOGY salmon is a fish; amniote does not appear in its derivation",
    why=("vertebrate -> fish on backbone, gills and fins is the whole chain. No "
         "amniotic egg is stated, so no amniote step can appear in it. The case "
         "asserts the route, not non-membership in Amniota, which the KB cannot "
         "derive."),
    goal=_g, bindings=_b,
    expect_value="fish",
    expect_order=_trait_chain("fish"),
    **BIOLOGY,
)

# --- felid side: envelope, F6 on a non-match -------------------------------

_g, _b = bio("tiger", chars={"body_length": 230, "geographic_range": "southeast_asia",
                             "habitat": "shrubland"})
case(
    name="BIOLOGY tiger resolves through the full felid envelope chain",
    why=("Panthera tigris: sourced head-body 150-230 cm, range includes Southeast "
         "Asia, habitat includes shrubland. 230/southeast_asia/shrubland sits in the "
         "tiger envelope and no other, so it pins to species. Entry at L6 via the "
         "is_felid premise, then four envelope levels to species."),
    goal=_g, bindings=_b,
    expect_value="tiger",
    expect_rules=set(_felid_chain("tiger")),
    expect_order=_felid_chain("tiger"),
    **BIOLOGY,
)

_g, _b = bio("tiger", chars={"body_length": 149, "geographic_range": "southeast_asia",
                             "habitat": "shrubland"})
case(
    name="BIOLOGY felid numeric lower bound excludes 1 cm below the range (F6)",
    why=("The tiger sourced minimum is 150 cm. At 149 cm the chained comparison "
         "body_min <= body_length <= body_max is false against the envelope read from "
         "KB data. Every character IS bound, so nothing is missing: F6, not F4 and "
         "not F5."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F6",
    **BIOLOGY,
)

_g, _b = bio("snow_leopard", chars={"body_length": 90, "geographic_range": "himalayas",
                                    "habitat": "shrubland"})
case(
    name="BIOLOGY snow leopard pinned by range where size alone cannot",
    why=("Panthera uncia (90-120 cm) and Lynx lynx (90-120 cm) have IDENTICAL sourced "
         "body ranges, so size cannot separate them. The sourced geographic ranges do: "
         "uncia is Himalayas/Mongolia, lynx is Eastern Europe/Russia/southern China."),
    goal=_g, bindings=_b,
    expect_value="snow_leopard",
    expect_order=_felid_chain("snow_leopard"),
    **BIOLOGY,
)

_g, _b = bio("cheetah", chars={"body_length": 113,
                               "geographic_range": "southern_africa",
                               "habitat": "desert"})
case(
    name="BIOLOGY cheetah resolves via Acinonyx, not Puma, though both are Puma-lineage",
    why=("Acinonyx jubatus and Puma concolor share the Puma lineage but are distinct "
         "genera. The cheetah sourced range (southern/eastern/central Africa; Iran) "
         "excludes the cougar range (South and North America). With universal rules a "
         "wrong-genus resolution cannot be forbidden by rule id, so the guarantee is "
         "expect_value: routing through Puma would return 'cougar'."),
    goal=_g, bindings=_b,
    expect_value="cheetah",
    expect_order=_felid_chain("cheetah"),
    **BIOLOGY,
)

_g, _b = bio("genus_lynx", chars={"body_length": 73,
                                  "geographic_range": "north_america",
                                  "habitat": "grassland"})
case(
    name="BIOLOGY genus Lynx derives where species cannot (Canada lynx case)",
    why=("Lynx canadensis (73-106 cm, North America, forest/shrubland/grassland) has a "
         "sourced envelope wholly inside Lynx rufus (50-120 cm, North America, those "
         "habitats plus desert and savanna), so NO observation separates them. The "
         "genus is the finest honest answer and must derive in its own right, so the "
         "chain stops at the genus level."),
    goal=_g, bindings=_b,
    expect_value="genus_lynx",
    expect_rules=set(_felid_chain("genus_lynx")),
    expect_order=_felid_chain("genus_lynx"),
    forbid_rules={"taxon_L10_from_L9_by_envelope"},
    **BIOLOGY,
)

_g, _b = bio("lion", chars={"body_length": 300,
                            "geographic_range": "sub_saharan_africa",
                            "habitat": "forest"})
case(
    name="BIOLOGY REFUSAL felid body length above every sourced felid (F6)",
    why=("The largest sourced head-body maximum is the lion 250 cm. At 300 cm the lion "
         "envelope fails, and so do the genus, lineage and subfamily envelopes (unions "
         "of their members), so the engine refuses rather than returning the nearest "
         "big cat."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F6",
    **BIOLOGY,
)

_g, _b = bio("domestic_cat", chars={"body_length": 46, "geographic_range": "himalayas",
                                    "habitat": "domesticated"})
case(
    name="BIOLOGY REFUSAL impossible felid character combination (F6)",
    why=("domesticated is the habitat token of Felis catus alone, whose sourced range "
         "is Worldwide. Pairing it with the Himalayas matches no sourced envelope."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F6",
    **BIOLOGY,
)

_g, _b = bio("ocelot", chars={"body_length": 102,
                              "geographic_range": "central_america",
                              "habitat": "forest"})
case(
    name="BIOLOGY no character manufactured and no primitive invented",
    why=("kb1_biology.json declares zero primitives, so every leaf of a derivation "
         "must be a supplied binding -- the characters plus the envelope values "
         "resolved from KB data. The generic grounded and no-re-derive checks enforce "
         "that on a full-depth Felinae chain."),
    goal=_g, bindings=_b,
    expect_value="ocelot",
    expect_order=_felid_chain("ocelot"),
    **BIOLOGY,
)

# --- the join: one derivation crossing traits -> envelope ------------------

_g, _b = bio("lion",
             traits=_T("has_backbone", "amniotic_egg", "produces_milk", "has_hair",
                       "live_birth", "has_placenta", "has_carnassial_teeth"),
             chars={"body_length": 193, "geographic_range": "sub_saharan_africa",
                    "habitat": "forest"},
             stop_at_entry=False)
case(
    name="BIOLOGY THE JOIN crosses trait-based upper tree into envelope felid sub-tree",
    why=("With BOTH the mammalian traits and the felid characters supplied, one "
         "derivation must run the full merged tree: vertebrate -> amniote -> mammal -> "
         "placental -> carnivore by TRAIT rules, across the L6 join, then pantherinae "
         "-> panthera_lineage -> genus_panthera -> lion by ENVELOPE rules. 10 rules, "
         "and it demonstrates one universal rule set spanning both discriminator "
         "kinds. Panthera leo: sourced 137-250 cm, sub-Saharan Africa, forest."),
    goal=_g, bindings=_b,
    expect_value="lion",
    expect_rules=set(_trait_chain("carnivore")
                     + ["taxon_L6_from_L5_join"]
                     + [f"taxon_L{n}_from_L{n-1}_by_envelope" for n in range(7, 11)]),
    expect_order=(_trait_chain("carnivore") + ["taxon_L6_from_L5_join"]
                  + [f"taxon_L{n}_from_L{n-1}_by_envelope" for n in range(7, 11)]),
    **BIOLOGY,
)

# ===========================================================================
# STOICHIOMETRY DOMAIN -- numeric, per-instance. Additive; physics, bio and
# felid cases above are untouched.
#
# Expectations are computed from the sourced values in domains/chemistry/source.json
# (IUPAC standard atomic weights; SI-exact Avogadro constant; molar volume at
# modern IUPAC STP; OpenStax Chemistry 2e Sec 4.1 balanced equations) -- never
# from engine output. Molar masses below are the sums of atomic weights, and the
# coefficients are the source equations, so each number is traceable.
#
# The rules under test are UNIVERSAL: none names a reaction or species. Reaction
# identity enters only through the role-named bindings, exactly as
# tools/eval/eval_chemistry.py resolves them from the KB reaction data.
# ===========================================================================

STOICH = {"kb": KB_STOICH}
_ST_CTX = "stoichiometry"

# Sourced molar masses, recomputed here from the IUPAC atomic weights.
_M_CH4 = 12.011 + 4 * 1.0080          # 16.0430
_M_CO2 = 12.011 + 2 * 15.999          # 44.0090
_M_H2O = 2 * 1.0080 + 15.999          # 18.0150
_M_N2 = 2 * 14.007                    # 28.0140
_M_H2 = 2 * 1.0080                    # 2.0160
_M_NH3 = 14.007 + 3 * 1.0080          # 17.0310
_AVOGADRO = 6.02214076e23             # SI exact since 20 May 2019
_VM_STP = 22.710954641485             # 0 degC, 100 kPa (modern IUPAC STP)


def st(goal_pred, unit, **binds):
    return {"predicate": goal_pred, "unit": unit, "context": _ST_CTX}, {
        k: {"value": v, "unit": ""} for k, v in binds.items()}


_g, _b = st("moles_reactant", "mol", mass_reactant=16.0430, molar_mass_reactant=_M_CH4)
case(
    name="STOICH moles from mass (n = m / M, one rule)",
    why=("n = m / M. One molar mass of CH4 (16.0430 g, the sum of the IUPAC atomic "
         "weights 12.011 + 4x1.0080) is exactly 1 mol."),
    goal=_g, bindings=_b,
    expect_value=1.0,
    expect_rules={"moles_from_mass"},
    expect_order=["moles_from_mass"],
    **STOICH,
)

_g, _b = st("mass_product", "g", mass_reactant=10.0, molar_mass_reactant=_M_CH4,
            coeff_reactant=1, coeff_product=1, molar_mass_product=_M_CO2,
            other_reactants="excess")
case(
    name="STOICH mass reactant -> mass product, the classic 3-rule chain",
    why=("CH4 + 2 O2 -> CO2 + 2 H2O (OpenStax Chemistry 2e Sec 4.1). "
         "n(CH4) = 10.0/16.0430 = 0.623325 mol; the coefficients give "
         "n(CO2) = n(CH4) x 1/1; m(CO2) = 0.623325 x 44.0090 = 27.4319 g. "
         "Chain: moles_from_mass -> mole_ratio -> mass_from_moles."),
    goal=_g, bindings=_b,
    expect_value=(10.0 / _M_CH4) * (1 / 1) * _M_CO2,
    expect_rules={"moles_from_mass", "mole_ratio", "mass_from_moles"},
    expect_order=["moles_from_mass", "mole_ratio", "mass_from_moles"],
    **STOICH,
)

_g, _b = st("mass_product", "g", mass_reactant=10.0, molar_mass_reactant=_M_CH4,
            coeff_reactant=1, coeff_product=2, molar_mass_product=_M_H2O,
            other_reactants="excess")
case(
    name="STOICH the coefficient is used: same reactant, coeff 2 product",
    why=("Same reaction and same 10.0 g of CH4, but the product is H2O with "
         "coefficient 2, so n(H2O) = n(CH4) x 2/1 and m = 1.246650 x 18.0150 = "
         "22.4584 g. Only the coefficient binding differs, which proves the mole "
         "ratio really comes from reaction DATA and not from the rule."),
    goal=_g, bindings=_b,
    expect_value=(10.0 / _M_CH4) * (2 / 1) * _M_H2O,
    expect_order=["moles_from_mass", "mole_ratio", "mass_from_moles"],
    **STOICH,
)

_g, _b = st("volume_gas_product", "L", mass_reactant=5.0, molar_mass_reactant=_M_CH4,
            coeff_reactant=1, coeff_product=1, gas_conditions="stp",
            other_reactants="excess")
case(
    name="STOICH gas volume at STP reads the molar-volume PRIMITIVE",
    why=("V = n x V_m. n(CO2) = 5.0/16.0430 = 0.311663 mol; the molar volume at "
         "modern IUPAC STP (0 degC, 100 kPa) is 22.710954641485 L/mol, held as a KB "
         "primitive, so V = 7.07815 L. molar_volume_stp is NOT supplied as a "
         "binding -- the engine must reach the primitive. STP is stated "
         "(gas_conditions = 'stp'), as the guard added 2026-11-21 requires."),
    goal=_g, bindings=_b,
    expect_value=(5.0 / _M_CH4) * _VM_STP,
    expect_rules={"moles_from_mass", "mole_ratio", "gas_volume_from_moles_stp"},
    expect_order=["moles_from_mass", "mole_ratio", "gas_volume_from_moles_stp"],
    **STOICH,
)

_g, _b = st("volume_gas_product", "L", mass_reactant=5.0, molar_mass_reactant=_M_CH4,
            coeff_reactant=1, coeff_product=1, other_reactants="excess")
case(
    name="AUDIT STOICH gas volume with conditions unstated is F5 naming gas_conditions",
    why=("The molar volume holds only at STP. A query that states no conditions must "
         "not be answered with the STP value: gas_conditions is unbound -> F5."),
    goal=_g, bindings=_b,
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="gas_conditions",
    **STOICH,
)

_g, _b = st("mass_product", "g", mass_reactant=10.0, molar_mass_reactant=_M_CH4,
            coeff_reactant=1, coeff_product=1, molar_mass_product=_M_CO2)
case(
    name="AUDIT STOICH excess of the other reactant unstated is F5 naming other_reactants",
    why=("'what mass of CO2 forms from 10.0 g of CH4' says nothing about the oxygen. The "
         "mole ratio gives the product amount only if CH4 is limiting, so mole_ratio's "
         "guard other_reactants == 'excess' is unmet -> F5 (2026-11-22)."),
    goal=_g, bindings=_b,
    expect_status="failure", expect_type="F5",
    expect_failure_predicate="other_reactants",
    **STOICH,
)

_g, _b = st("particles_product", "particles", mass_reactant=2.0,
            molar_mass_reactant=_M_CH4, coeff_reactant=1, coeff_product=1,
            other_reactants="excess")
case(
    name="STOICH particle count reads the Avogadro PRIMITIVE",
    why=("N = n x N_A with N_A = 6.02214076e23 mol^-1, exact since the 2019 SI "
         "revision and held as a KB primitive. n(CO2) = 2.0/16.0430 = 0.124665 mol, "
         "so N = 7.5075e22."),
    goal=_g, bindings=_b,
    expect_value=(2.0 / _M_CH4) * _AVOGADRO,
    expect_order=["moles_from_mass", "mole_ratio", "particles_from_moles"],
    **STOICH,
)

_g, _b = st("theoretical_mass_product", "g",
            mass_reactant_a=28.0, molar_mass_reactant_a=_M_N2, coeff_reactant_a=1,
            mass_reactant_b=3.0, molar_mass_reactant_b=_M_H2, coeff_reactant_b=3,
            coeff_product=2, molar_mass_product=_M_NH3)
case(
    name="STOICH limiting reactant: GUARD selects reactant B (H2 limits)",
    why=("N2 + 3 H2 -> 2 NH3 (OpenStax Sec 4.1). n(N2) = 28.0/28.0140 = 0.99950, "
         "n(H2) = 3.0/2.0160 = 1.48810. Reaction units: N2 gives 0.99950/1 = 0.99950, "
         "H2 gives 1.48810/3 = 0.496032. H2 is smaller, so H2 limits and the "
         "reactant_b rule's guard is the one that holds. "
         "n(NH3) = 0.496032 x 2 = 0.992063; m = 16.8958 g."),
    goal=_g, bindings=_b,
    expect_value=(3.0 / _M_H2) / 3 * 2 * _M_NH3,
    expect_rules={"moles_a_from_mass", "moles_b_from_mass",
                  "limiting_product_from_reactant_b",
                  "theoretical_mass_from_limited_moles"},
    forbid_rules={"limiting_product_from_reactant_a"},
    **STOICH,
)

_g, _b = st("theoretical_mass_product", "g",
            mass_reactant_a=16.0, molar_mass_reactant_a=_M_CH4, coeff_reactant_a=1,
            mass_reactant_b=128.0, molar_mass_reactant_b=2 * 15.999, coeff_reactant_b=2,
            coeff_product=1, molar_mass_product=_M_CO2)
case(
    name="STOICH limiting reactant: GUARD selects reactant A (CH4 limits)",
    why=("CH4 + 2 O2 -> CO2 + 2 H2O. n(CH4) = 16.0/16.0430 = 0.997319 units; "
         "n(O2) = 128.0/31.998 = 4.00025, giving 4.00025/2 = 2.000125 units. CH4 is "
         "smaller, so the reactant_a guard holds and the complementary rule must NOT "
         "fire. m(CO2) = 0.997319 x 44.0090 = 43.891 g. The two guards are mutually "
         "exclusive, so this and the previous case prove selection is by guard alone."),
    goal=_g, bindings=_b,
    expect_value=(16.0 / _M_CH4) * _M_CO2,
    expect_rules={"moles_a_from_mass", "moles_b_from_mass",
                  "limiting_product_from_reactant_a",
                  "theoretical_mass_from_limited_moles"},
    forbid_rules={"limiting_product_from_reactant_b"},
    **STOICH,
)

_g, _b = st("percent_yield", "percent",
            mass_reactant_a=28.0, molar_mass_reactant_a=_M_N2, coeff_reactant_a=1,
            mass_reactant_b=3.0, molar_mass_reactant_b=_M_H2, coeff_reactant_b=3,
            coeff_product=2, molar_mass_product=_M_NH3, actual_mass_product=15.0)
case(
    name="STOICH percent yield, full 5-rule chain",
    why=("Theoretical yield 16.8958 g as derived above; actual 15.0 g. "
         "percent yield = 15.0/16.8958 x 100 = 88.7793 %. Chain: both reactant "
         "mole conversions, the limiting guard, theoretical mass, percent yield."),
    goal=_g, bindings=_b,
    expect_value=15.0 / ((3.0 / _M_H2) / 3 * 2 * _M_NH3) * 100,
    expect_rules={"moles_a_from_mass", "moles_b_from_mass",
                  "limiting_product_from_reactant_b",
                  "theoretical_mass_from_limited_moles",
                  "percent_yield_from_masses"},
    **STOICH,
)

_g, _b = st("mass_product", "g", molar_mass_reactant=_M_CH4,
            coeff_reactant=1, coeff_product=1, molar_mass_product=_M_CO2)
case(
    name="STOICH REFUSAL no amount supplied",
    why=("Everything about the reaction is known but no quantity was given. "
         "mass_reactant is unbound and is the output of no rule, so the engine must "
         "refuse with the F5 certificate rather than assume a basis."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F5",
    **STOICH,
)

_g, _b = st("theoretical_mass_product", "g",
            mass_reactant_a=28.0, molar_mass_reactant_a=_M_N2, coeff_reactant_a=1,
            molar_mass_reactant_b=_M_H2, coeff_reactant_b=3,
            coeff_product=2, molar_mass_product=_M_NH3)
case(
    name="STOICH REFUSAL second reactant amount missing, so no guard is evaluable",
    why=("A limiting-reactant comparison needs both amounts. mass_reactant_b is "
         "unsupplied, so moles_reactant_b cannot be derived and NEITHER limiting "
         "guard can even be evaluated. The refusal must be F5 (a required value is "
         "missing), not F6 (which would wrongly claim the guards were checked and "
         "did not hold)."),
    goal=_g, bindings=_b,
    expect_status="failure",
    expect_type="F5",
    **STOICH,
)


# ---------------------------------------------------------------------------
# Checking machinery
# ---------------------------------------------------------------------------
def effectively_supplied(bindings: dict, predicate: str, kb: dict) -> bool:
    """True if `predicate` resolves to a supplied binding, directly or by alias.

    Mirrors engine._lookup_binding: a bound `net_force` satisfies `force`, so a
    rule producing `force` in that situation IS re-deriving a supplied quantity.

    Aliases come from the SAME place the engine reads them -- the loaded KB's
    `predicate_aliases` field, via engine.predicate_aliases -- so this guard
    cannot silently drift out of step with the engine's own lookup. A KB
    declaring no aliases makes this an exact-name check.
    """
    if predicate in bindings:
        return True
    return any(a in bindings for a in predicate_aliases(kb).get(predicate, ()))


def rule_ids(result: dict) -> list[str]:
    return [s["id"] for s in result.get("derivation", []) if s["type"] == "rule"]


def check(c: dict) -> list[str]:
    """Run one case. Returns a list of failure messages (empty == pass)."""
    errs: list[str] = []
    bindings = c.get("bindings", {})
    kb = c.get("kb", KB)

    if c.get("stage") == "parser":
        # Parser-stage validation (non-triviality, Part C): no engine call.
        from pct.parser import Parser
        p = Parser(c.get("kb2", DOMAINS["physics"]["kb2"]))
        goal = dict(c["goal"], bindings=bindings)
        reason = p._validate(goal)
        c["_result"] = ({"status": "failure", "type": "F4", "stage": "parser",
                         "reason": reason} if reason else {"status": "success"})
        c["_elapsed"] = 0.0
        if c.get("expect_status", "failure") == "failure":
            if reason is None:
                errs.append("parser validation accepted the goal state; expected F4")
            elif c.get("expect_reason") and reason != c["expect_reason"]:
                errs.append(f"F4 reason {reason!r}, expected {c['expect_reason']!r}")
        elif reason is not None:
            errs.append(f"parser validation rejected the goal state: {reason!r}")
        return errs
    rule_output = _rule_output(kb)
    primitive_ids = _primitive_ids(kb)

    t0 = time.time()
    result = prove(
        dict(c["goal"]), bindings, kb,
        d_max=c.get("d_max", 5), c_max=c.get("c_max", 100),
    )
    elapsed = time.time() - t0
    c["_result"] = result
    c["_elapsed"] = elapsed

    if "max_seconds" in c and elapsed > c["max_seconds"]:
        errs.append(f"took {elapsed:.3f}s, expected under {c['max_seconds']}s")

    expect_status = c.get("expect_status", "success")
    status = result.get("status")
    if status != expect_status:
        errs.append(f"status {status!r}, expected {expect_status!r}")
        return errs  # nothing further is meaningful

    if expect_status == "failure":
        if "expect_type" in c and result.get("type") != c["expect_type"]:
            errs.append(f"failure type {result.get('type')!r}, expected {c['expect_type']!r}")
        if "expect_failure_predicate" in c:
            named = (result.get("goal") or {}).get("predicate")
            if named != c["expect_failure_predicate"]:
                errs.append(f"failure names {named!r}, expected "
                            f"{c['expect_failure_predicate']!r}")
        if "expect_f5_candidates" in c:
            got = {(e["rule"], e["missing"]) for e in result.get("candidates") or []}
            if got != c["expect_f5_candidates"]:
                errs.append(f"F5 candidates {sorted(got)}, expected "
                            f"{sorted(c['expect_f5_candidates'])}")
        if c.get("expect_cycle") and not result.get("cycle"):
            errs.append(
                f"expected the cycle guard to fire (cycle=True); got "
                f"reason={result.get('reason')!r}"
            )
        return errs

    # ---- success path ----
    if "expect_value" in c:
        exp, got = c["expect_value"], result.get("value")
        if isinstance(exp, str):
            # Categorical domain: the value IS the taxon label.
            if got != exp:
                errs.append(f"value {got!r}, expected {exp!r}")
        elif not isinstance(got, (int, float)):
            errs.append(f"value {got!r} is not numeric")
        elif exp == 0:
            if abs(got) > 1e-6:
                errs.append(f"value {got!r}, expected ~0")
        elif abs(got - exp) / abs(exp) > 0.01:
            errs.append(f"value {got!r}, expected {exp!r} (>1% relative error)")

    fired = rule_ids(result)

    if "expect_rules" in c and set(fired) != c["expect_rules"]:
        missing = c["expect_rules"] - set(fired)
        extra = set(fired) - c["expect_rules"]
        parts = []
        if missing:
            parts.append(f"missing {sorted(missing)}")
        if extra:
            parts.append(f"unexpected {sorted(extra)}")
        errs.append("rule set mismatch: " + "; ".join(parts))

    if "expect_order" in c and fired != c["expect_order"]:
        errs.append(f"rule order {fired}, expected {c['expect_order']}")

    for bad in c.get("forbid_rules", set()):
        if bad in fired:
            errs.append(f"forbidden rule {bad!r} fired")

    used = {s["predicate"] for s in result.get("derivation", []) if s["type"] == "binding"}
    for need in c.get("expect_binding_predicates", set()):
        if need not in used:
            errs.append(f"binding {need!r} was not used by the derivation")

    # No rule may produce a quantity that was supplied.
    for rid in fired:
        out_pred = rule_output.get(rid)
        if out_pred and effectively_supplied(bindings, out_pred, kb):
            errs.append(
                f"rule {rid!r} DERIVED {out_pred!r}, which was supplied as a binding"
            )

    for bad in c.get("forbid_derived_predicates", set()):
        for rid in fired:
            if rule_output.get(rid) == bad:
                errs.append(f"rule {rid!r} derived forbidden predicate {bad!r}")

    # Every leaf must be grounded.
    for s in result.get("derivation", []):
        if s["type"] == "binding":
            if not effectively_supplied(bindings, s["predicate"], kb):
                errs.append(
                    f"binding step for {s['predicate']!r} does not trace to a supplied input"
                )
        elif s["type"] == "primitive":
            if s["id"] not in primitive_ids:
                errs.append(f"primitive step {s['id']!r} is not a KB primitive")
            if s["id"] in c.get("forbid_primitive_ids", set()):
                errs.append(f"forbidden primitive {s['id']!r} used")

    return errs


def _fmt(v) -> str:
    """Format a derivation value without assuming it is numeric."""
    return f"{v:.6g}" if isinstance(v, float) else repr(v)


def describe(result: dict) -> str:
    if result.get("status") != "success":
        out = f"    FAILURE {result.get('type')}  reason={result.get('reason')!r}"
        if result.get("cycle"):
            out += "  (cycle guard fired)"
        return out
    lines = [f"    SUCCESS value={result.get('value')!r} {result.get('unit')}"]
    for s in result.get("derivation", []):
        if s["type"] == "rule":
            ins = ", ".join(f"{k}={_fmt(v['value'])}" for k, v in s["inputs"].items())
            lines.append(
                f"      [{s['step']}] rule      {s['id']}: {s['formula']}"
                f"  ({ins})  ->  {_fmt(s['output'])}"
            )
        else:
            lines.append(
                f"      [{s['step']}] {s['type']:<9} {s.get('predicate')} = "
                f"{_fmt(s['value'])} {s['unit']}"
            )
    return "\n".join(lines)


def main() -> int:
    print("=" * 78)
    print("PCT ENGINE SOUNDNESS SUITE  --  derivation structure, not just value")
    print("=" * 78)

    failures = []
    for c in CASES:
        errs = check(c)
        mark = "PASS" if not errs else "FAIL"
        print(f"[{mark}] {c['name']}")
        if errs:
            failures.append((c, errs))

    print()
    print("-" * 78)
    passed = len(CASES) - len(failures)
    print(f"{passed}/{len(CASES)} cases passed")

    if failures:
        print()
        print("=" * 78)
        print("FAILING CASES IN DETAIL")
        print("=" * 78)
        for c, errs in failures:
            print()
            print(f"### {c['name']}")
            print(f"  rationale: {c['why']}")
            print(f"  goal     : {c['goal']}")
            print(f"  bindings : "
                  f"{ {k: v['value'] for k, v in c.get('bindings', {}).items()} }")
            if "d_max" in c:
                print(f"  d_max    : {c['d_max']}")
            print("  expected :")
            if c.get("expect_status", "success") == "failure":
                print(f"    FAILURE type={c.get('expect_type')}"
                      + ("  with cycle guard" if c.get("expect_cycle") else ""))
            else:
                if "expect_value" in c:
                    print(f"    value {c['expect_value']!r}")
                if "expect_order" in c:
                    print(f"    rules {c['expect_order']}")
                elif "expect_rules" in c:
                    print(f"    rules {sorted(c['expect_rules'])}")
            print("  actual   :")
            print(describe(c["_result"]))
            print(f"  elapsed  : {c['_elapsed']:.4f}s")
            print("  problems :")
            for e in errs:
                print(f"    - {e}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
