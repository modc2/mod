"""
Tests for shielded bridging, the lessons, and the answering agent.

The bridging tests pin one property above all others: an address this module
hands to a bridge must be payable ONLY into a shielded pool this module can
actually read. Every other check here is about not lying to a beginner --
cross-references that resolve, intents that route to the lesson they claim,
and an agent that never volunteers a call that spends.

Network-dependent tests are marked `live`:
    pytest tests/ -m "not live"     # offline only
"""

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ.setdefault("ZCASH_WALLET_DIR", tempfile.mkdtemp())

from zcash import agent, bridge, learn, sapling  # noqa: E402

live = pytest.mark.live

_XSK = sapling.ExtendedSpendingKey.from_seed(bytes(range(32)))
_Z_ADDRESS = _XSK.address(0).encode()
_SAPLING_RAW = sapling.decode_payment_address(_Z_ADDRESS).raw
_T_HASH160 = bytes(range(20))

_SAPLING_ONLY_UA = sapling.encode_unified_address(
    [(sapling.TYPECODE_SAPLING, _SAPLING_RAW)])
_MIXED_UA = sapling.encode_unified_address(
    [(sapling.TYPECODE_P2PKH, _T_HASH160),
     (sapling.TYPECODE_SAPLING, _SAPLING_RAW)])
_ORCHARD_UA = sapling.encode_unified_address(
    [(sapling.TYPECODE_SAPLING, _SAPLING_RAW),
     (sapling.TYPECODE_ORCHARD, bytes(range(43)))])


# ── The recipient rewrite ───────────────────────────────────────────────────

def test_bare_sapling_address_is_wrapped_into_a_unified_address():
    """The router rejects zs1, so a zs1 has to go out as a UA -- and it has to
    be the SAME receiver, or the money lands somewhere else entirely."""
    out = bridge.shielded_recipient(_Z_ADDRESS)
    assert out["rewritten"] is True
    assert out["recipient"].startswith("u1")
    assert sapling.decode_unified_address(out["recipient"]) == [
        (sapling.TYPECODE_SAPLING, _SAPLING_RAW)]


def test_transparent_receiver_is_stripped_from_a_mixed_unified_address():
    """This is the whole point. A UA offering both receivers is a transparent
    destination in practice, because the sender picks."""
    out = bridge.shielded_recipient(_MIXED_UA)
    assert out["rewritten"] is True
    assert out["dropped_receivers"] == ["p2pkh"]
    receivers = sapling.decode_unified_address(out["recipient"])
    assert [tc for tc, _ in receivers] == [sapling.TYPECODE_SAPLING]


def test_shielded_only_address_passes_through_unchanged():
    out = bridge.shielded_recipient(_SAPLING_ONLY_UA)
    assert out["rewritten"] is False
    assert out["recipient"] == _SAPLING_ONLY_UA


def test_transparent_address_is_refused_rather_than_quietly_published():
    with pytest.raises(bridge.BridgeError) as e:
        bridge.shielded_recipient("t1KfLLnDdRvSjbdCbTKyEyEEo2vP2ZDXbYP")
    assert "transparent" in str(e.value).lower()


def test_unreadable_pool_is_stripped_when_readable_is_given():
    """A receiver we cannot decrypt is worse than no receiver: the funds
    arrive, and every balance this module can show says zero."""
    out = bridge.shielded_recipient(_ORCHARD_UA, readable={"sapling"})
    assert out["unreadable_receivers"] == ["orchard"]
    assert [tc for tc, _ in sapling.decode_unified_address(out["recipient"])] \
        == [sapling.TYPECODE_SAPLING]


def test_unreadable_pool_is_kept_when_it_becomes_readable():
    """The rule is capability-driven, not a hardcoded ban on Orchard."""
    out = bridge.shielded_recipient(_ORCHARD_UA, readable={"sapling", "orchard"})
    assert not out.get("unreadable_receivers")
    assert set(out["pools"]) == {"sapling", "orchard"}


def test_address_with_no_readable_pool_is_refused():
    orchard_only = sapling.encode_unified_address(
        [(sapling.TYPECODE_ORCHARD, bytes(range(43)))])
    with pytest.raises(bridge.BridgeError) as e:
        bridge.shielded_recipient(orchard_only, readable={"sapling"})
    assert "cannot read" in str(e.value)


def test_unified_address_without_a_shielded_receiver_is_refused():
    with pytest.raises(ValueError):
        # ZIP-316 itself forbids this shape, so it cannot even be constructed.
        sapling.encode_unified_address([(sapling.TYPECODE_P2PKH, _T_HASH160)])


def test_shielded_refund_is_refused_on_the_way_out():
    """A refund is paid by the solver, and no solver can pay into the pool."""
    with pytest.raises(bridge.BridgeError) as e:
        bridge.shielded_out_quote("ETH", 1, "0x" + "11" * 20, _Z_ADDRESS)
    assert "transparent" in str(e.value)


def test_privacy_is_honest_about_the_outbound_direction():
    out = bridge.privacy("out", "eth")
    assert out["grade"] == "weak"
    assert any("public" in v or "clear" in v for v in out["visible"])
    inbound = bridge.privacy("in", "eth")
    assert inbound["grade"] == "good"
    # Even the good direction must name what still leaks.
    assert inbound["visible"]


def test_plan_reports_out_as_unsupported_without_a_node():
    assert bridge.shielded_plan(has_node=False)["out"]["supported"] is False
    assert bridge.shielded_plan(has_node=True)["out"]["supported"] is True
    assert bridge.shielded_plan(has_node=False)["in"]["supported"] is True


# ── Lessons ─────────────────────────────────────────────────────────────────

def test_every_lesson_link_resolves():
    for lesson in learn.LESSONS:
        assert lesson["next"] is None or lesson["next"] in learn.LESSON_INDEX
        assert lesson["level"] in ("start", "core", "deep")
        for term in lesson["terms"]:
            key = learn.ALIASES.get(term.lower(), term.lower())
            assert key in learn.GLOSSARY, f"{lesson['id']} cites {term!r}"


def test_every_glossary_entry_points_at_a_real_lesson():
    for term, (_, see) in learn.GLOSSARY.items():
        assert see in learn.LESSON_INDEX, f"{term} -> {see}"


def test_every_reading_path_names_real_lessons():
    for path, ids in learn.PATHS.items():
        for lesson_id in ids:
            assert lesson_id in learn.LESSON_INDEX, f"{path} -> {lesson_id}"


def test_lesson_lookup_is_forgiving():
    assert learn.lesson("private-bridging")["id"] == "private-bridging"
    assert learn.lesson("bridging privately")["id"] in learn.LESSON_INDEX
    with pytest.raises(KeyError):
        learn.lesson("quantum tunnelling")


def test_glossary_understands_how_beginners_type():
    for typed, expected in [("zaddr", "z-address"), ("gas", "zip-317"),
                            ("seed", "seed phrase"), ("snark", "zk-snark"),
                            ("mnemonic", "seed phrase"), ("UA", "unified address")]:
        assert learn.explain(typed)["term"] == expected


def test_lessons_do_not_promise_shielded_spending():
    """A lesson that implies this module can spend shielded ZEC would cost
    someone money.

    Scoped to sentences that make a CLAIM OF ABILITY -- 'shielded spends' as a
    noun, counting them in a transaction, is a description and fine. A sentence
    saying something *can* spend shielded funds has to carry the qualifier in
    the same breath, not three paragraphs later.
    """
    ability = ("can ", "able to", "lets you", "let you", "you may", "will ")
    qualified = ("groth16", "proof", "proving", "node", "cannot", "can not",
                 "not", "without", "needs", "export", "another wallet")
    for lesson in learn.LESSONS:
        for sentence in " ".join(lesson["body"]).lower().split("."):
            if "spend" not in sentence or "shielded" not in sentence:
                continue
            if not any(a in sentence for a in ability):
                continue
            assert any(w in sentence for w in qualified), \
                f"{lesson['id']}: {sentence.strip()!r}"


# ── Agent ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,intent", [
    ("how do I bridge USDC into a shielded address?", "bridge-in-shielded"),
    ("can I bridge eth into zcash privately", "bridge-in-shielded"),
    ("can I swap my shielded ZEC for ETH privately", "bridge-out-shielded"),
    ("how do I cash out of the shielded pool", "bridge-out-shielded"),
    ("why can't I send shielded zec", "cannot-send-shielded"),
    ("how do I shield my funds", "cannot-send-shielded"),
    ("what's the difference between t1 and zs1", "which-address"),
    ("is my z-address safe to share", "safe-to-share"),
    ("I lost my seed phrase", "lost-seed"),
    ("how private is zcash really", "how-private"),
    ("how much are fees", "fees"),
    ("it says dry run and nothing happened", "dry-run"),
    ("my shielded balance is zero", "balance-missing"),
    ("which chains can I bridge to", "bridge-basics"),
    ("what is a viewing key", "define"),
])
def test_agent_routes_real_questions(question, intent):
    assert agent.ask(question)["intent"] == intent


def test_agent_distinguishes_the_two_bridge_directions():
    """These two questions share almost every word and need opposite answers.

    One is the direction that works with nothing extra; the other is the one
    that needs a proving node and cannot be private. Conflating them is the
    single most expensive mistake this agent could make."""
    inbound = agent.ask("bridge usdc into my shielded address")
    outbound = agent.ask("bridge my shielded zec out to usdc")
    assert inbound["intent"] == "bridge-in-shielded"
    assert outbound["intent"] == "bridge-out-shielded"
    assert "cannot be private" in outbound["answer"] or \
           "cannot be made private" in outbound["answer"]


def test_agent_says_it_does_not_know_rather_than_guessing():
    for off_topic in ("what's the weather in paris",
                      "what is the capital of france",
                      "write me a poem"):
        out = agent.ask(off_topic)
        assert out["confidence"] == "none"
        assert out["intent"] is None


def test_agent_cites_lessons_that_exist():
    for intent in agent.INTENTS:
        assert intent["lesson"] in learn.LESSON_INDEX, intent["id"]
        for fn in intent.get("ground", []):
            assert fn in agent.GROUNDING_FNS, f"{intent['id']} -> {fn}"


def test_agent_never_grounds_on_a_function_that_spends():
    """`ground` runs functions by itself. The allowlist is the only thing
    standing between a question and an unasked-for transaction."""
    forbidden = {"send", "broadcast_raw", "bridge_send", "bridge_start",
                 "shielded_send", "wallet_reveal", "shielded_export",
                 "wallet_delete", "bridge_shielded_in", "bridge_shielded_out"}
    assert not (agent.GROUNDING_FNS & forbidden)


def test_agent_marks_spending_actions_as_guarded():
    """Suggested calls that move money or reveal secrets must be flagged, so a
    console renders them differently from a free read."""
    spending = {"send", "wallet_create", "wallet_restore", "shielded_export",
                "shielded_scan", "shielded_balance", "bridge_shielded_out"}
    for intent in agent.INTENTS:
        for action in intent.get("actions", []):
            if action.get("fn") in spending:
                assert action.get("guarded") is True, \
                    f"{intent['id']}: {action['fn']}"


def test_agent_drops_actions_naming_functions_that_do_not_exist():
    class Stub:
        def learn(self):
            pass

    kept = agent._check_actions(
        [{"fn": "learn"}, {"fn": "no_such_function"}], Stub())
    assert [a["fn"] for a in kept] == ["learn"]


def test_agent_works_without_a_model():
    """The whole corpus is local. A deployment with no LLM key still answers."""
    os.environ.pop("ZCASH_LLM_URL", None)
    out = agent.ask("what is a z-address")
    assert out["source"] in ("glossary", "lessons")
    assert out["answer"]
    assert agent.status()["model"]["configured"] is False


def test_agent_grounding_survives_a_broken_module():
    class Broken:
        def info(self):
            raise RuntimeError("upstream down")

    out = agent.ask("teach me zcash", mod=Broken())
    assert out["answer"]                       # the lesson still answers
    assert "info" in out.get("grounding_errors", {})


# ── Live ────────────────────────────────────────────────────────────────────

@live
def test_router_accepts_a_shielded_only_unified_address():
    """The claim the inbound route rests on. If this fails, the router changed
    its mind about unified addresses and bridge_shielded_in is broken."""
    quote = bridge.shielded_quote("eth:USDC", 25, _Z_ADDRESS,
                                  "0x" + "11" * 20, dry=True)
    assert quote["to"] == "zec:ZEC"
    assert quote["shielded"] is True
    assert quote["destination_pool"] == "sapling"
    assert float(quote["amount_out"]) > 0


@live
def test_router_still_rejects_a_bare_sapling_address():
    """The reason the wrapping exists. If this ever starts passing, the wrap is
    no longer load-bearing -- but leaving it in costs nothing."""
    with pytest.raises(bridge.BridgeError):
        bridge.quote("eth:USDC", bridge.ZEC_ASSET, 25, _Z_ADDRESS,
                     "0x" + "11" * 20, dry=True)
