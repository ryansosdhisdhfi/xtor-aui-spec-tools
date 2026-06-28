#!/usr/bin/env python3
from __future__ import annotations

import argparse
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
APITEST = ROOT / "apitest"
if str(APITEST) not in sys.path:
    sys.path.insert(0, str(APITEST))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from figure_describe_core import FigureMeta, build_prompt, normalize_unknowns  # noqa: E402
import inject_figure_enrichment as inject  # noqa: E402


class PromptGuidanceTests(unittest.TestCase):
    def test_prompt_distinguishes_static_block_diagrams_from_transactions(self) -> None:
        meta = FigureMeta(
            image_id="fig_block",
            doc_id="doc",
            page=1,
            section="PHY/MAC",
            image_type="block interface diagram",
            ocr_text="TxData RxData Command Status",
            image_path="image.png",
            document_context="Static PHY/MAC interface block diagram.",
        )
        prompt = build_prompt(meta)

        self.assertIn("static block/interface diagrams", prompt)
        self.assertIn("waveform/timing diagrams", prompt)
        self.assertIn("protocol/sequence/message-bus diagrams", prompt)
        self.assertIn("transactions", prompt)
        self.assertIn("empty unless", prompt)
        self.assertIn('"figure_kind"', prompt)
        self.assertIn('"figure_kind_confidence"', prompt)
        self.assertIn('"extraction_profile"', prompt)

    def test_waveform_pseudo_transactions_are_pruned_by_normalization(self) -> None:
        normalized = normalize_unknowns(
            {
                "image_type": "timing diagram",
                "digital_ic_semantics": {
                    "signals": [{"name": "RxValid"}],
                    "interfaces": [],
                    "transactions": [
                        {
                            "order": 1,
                            "bus": "unknown",
                            "producer": "Rx+/Rx-",
                            "consumer": "unknown",
                            "operation": "data reception ends",
                            "target": "unknown",
                            "payload_or_purpose": "transition to idle",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "waveform goes flat",
                        },
                        {
                            "order": 2,
                            "bus": "unknown",
                            "producer": "RxElecIdle",
                            "consumer": "unknown",
                            "operation": "assert",
                            "target": "unknown",
                            "payload_or_purpose": "indicate idle",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "signal rises",
                        },
                    ],
                    "timing_constraints": [{"name": "ordering"}],
                    "phases": [{"order": 1, "name": "idle transition"}],
                    "assumptions": [],
                    "uncertain_items": [],
                },
            }
        )
        self.assertEqual(normalized["digital_ic_semantics"]["transactions"], [])

    def test_waveform_transactions_with_generic_interface_bus_are_pruned(self) -> None:
        normalized = normalize_unknowns(
            {
                "image_type": "timing diagram",
                "digital_ic_semantics": {
                    "signals": [{"name": "RxValid"}],
                    "interfaces": [{"name": "receive interface"}],
                    "transactions": [
                        {
                            "order": 1,
                            "bus": "receive interface",
                            "producer": "Rx+/Rx-",
                            "consumer": "unknown",
                            "operation": "active receive data ends",
                            "target": "unknown",
                            "payload_or_purpose": "transition from received data to electrical idle",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "Rx+/Rx- Data then flat idle",
                        },
                        {
                            "order": 2,
                            "bus": "receive interface",
                            "producer": "RxValid",
                            "consumer": "unknown",
                            "operation": "deassert",
                            "target": "unknown",
                            "payload_or_purpose": "indicate received data no longer valid",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "RxValid goes low after RxElecIdle asserts",
                        },
                    ],
                    "timing_constraints": [{"name": "ordering"}],
                    "phases": [{"order": 1, "name": "idle transition"}],
                    "assumptions": [],
                    "uncertain_items": [],
                },
            }
        )
        self.assertEqual(normalized["digital_ic_semantics"]["transactions"], [])

    def test_waveform_observation_transactions_without_commit_are_pruned(self) -> None:
        normalized = normalize_unknowns(
            {
                "image_type": "timing diagram",
                "digital_ic_semantics": {
                    "signals": [{"name": "RxElecIdle"}],
                    "interfaces": [],
                    "transactions": [
                        {
                            "order": 1,
                            "bus": "unknown",
                            "producer": "unknown",
                            "consumer": "unknown",
                            "operation": "receive data active",
                            "target": "Rx+/Rx- and RxData[]",
                            "payload_or_purpose": "Data",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "Data is present initially",
                        },
                        {
                            "order": 2,
                            "bus": "unknown",
                            "producer": "unknown",
                            "consumer": "unknown",
                            "operation": "transition to idle",
                            "target": "receiver interface",
                            "payload_or_purpose": "end of received data activity",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "receive pair becomes idle first, then RxElecIdle asserts",
                        },
                    ],
                    "timing_constraints": [{"name": "ordering"}],
                    "phases": [{"order": 1, "name": "active receive"}],
                    "assumptions": [],
                    "uncertain_items": [],
                },
            }
        )
        self.assertEqual(normalized["digital_ic_semantics"]["transactions"], [])

    def test_waveform_transition_to_idle_observation_is_pruned(self) -> None:
        normalized = normalize_unknowns(
            {
                "image_type": "timing diagram",
                "digital_ic_semantics": {
                    "signals": [{"name": "RxValid"}],
                    "interfaces": [],
                    "transactions": [
                        {
                            "order": 1,
                            "bus": "receive interface",
                            "producer": "unknown",
                            "consumer": "unknown",
                            "operation": "receive data then enter idle",
                            "target": "unknown",
                            "payload_or_purpose": "Data",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "receiver transitions from active data reception to idle",
                        }
                    ],
                    "timing_constraints": [{"name": "ordering"}],
                    "phases": [{"order": 1, "name": "transition to idle"}],
                    "assumptions": [],
                    "uncertain_items": [],
                },
            }
        )
        self.assertEqual(normalized["digital_ic_semantics"]["transactions"], [])

    def test_message_bus_framing_substeps_are_not_kept_as_transactions(self) -> None:
        normalized = normalize_unknowns(
            {
                "image_type": "timing diagram",
                "digital_ic_semantics": {
                    "signals": [{"name": "M2P_MessageBus[7:0]"}],
                    "interfaces": [],
                    "transactions": [
                        {
                            "order": 1,
                            "bus": "M2P_MessageBus[7:0]",
                            "producer": "unknown",
                            "consumer": "unknown",
                            "operation": "write",
                            "target": "addr[11:0]",
                            "payload_or_purpose": "phase 1: Cmd[3:0] and addr[11:8]",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "write takes 3 cycles",
                        },
                        {
                            "order": 2,
                            "bus": "M2P_MessageBus[7:0]",
                            "producer": "unknown",
                            "consumer": "unknown",
                            "operation": "write",
                            "target": "addr[11:0]",
                            "payload_or_purpose": "phase 2: addr[7:0]",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "write takes 3 cycles",
                        },
                        {
                            "order": 3,
                            "bus": "M2P_MessageBus[7:0]",
                            "producer": "unknown",
                            "consumer": "unknown",
                            "operation": "write",
                            "target": "addr[11:0]",
                            "payload_or_purpose": "phase 3: data[7:0]",
                            "commit": "unknown",
                            "source": "visible_image",
                            "evidence_text": "write takes 3 cycles",
                        },
                    ],
                    "timing_constraints": [{"name": "write duration"}],
                    "phases": [{"order": 1, "name": "Write phase 1"}],
                    "assumptions": [],
                    "uncertain_items": [],
                },
            }
        )
        self.assertEqual(normalized["digital_ic_semantics"]["transactions"], [])

    def test_protocol_transactions_are_preserved_by_normalization(self) -> None:
        normalized = normalize_unknowns(
            {
                "image_type": "timing diagram",
                "digital_ic_semantics": {
                    "signals": [{"name": "M2P_MessageBus[7:0]"}],
                    "interfaces": [],
                    "transactions": [
                        {
                            "order": 1,
                            "bus": "M2P_MessageBus[7:0]",
                            "producer": "Controller",
                            "consumer": "PHY",
                            "operation": "write",
                            "target": "PHY TX Control5",
                            "payload_or_purpose": "request coefficients",
                            "commit": "Wr Com",
                            "source": "visible_image",
                            "evidence_text": "Wr Com",
                        }
                    ],
                    "timing_constraints": [{"name": "response time"}],
                    "phases": [{"order": 1, "name": "request"}],
                    "assumptions": [],
                    "uncertain_items": [],
                },
            }
        )
        self.assertEqual(len(normalized["digital_ic_semantics"]["transactions"]), 1)

    def test_figure_kind_metadata_defaults_exist(self) -> None:
        normalized = normalize_unknowns({})
        self.assertEqual(normalized["figure_kind"], "unknown")
        self.assertEqual(normalized["figure_kind_confidence"], 0.0)
        self.assertEqual(normalized["extraction_profile"], "default_v1")


class EnrichmentRenderingTests(unittest.TestCase):
    def test_render_block_surfaces_structured_semantics(self) -> None:
        data = {
            "image_id": "fig_demo",
            "title": "Structured Semantics Demo",
            "summary": "A compact figure with signal, transaction, timing, and assumption data.",
            "keywords": ["timing", "control"],
            "ocr_text": "REQ ACK 64ns",
            "digital_ic_semantics": {
                "signals": [
                    {
                        "name": "REQ",
                        "type": "control",
                        "producer": "Requester",
                        "consumer": "Responder",
                        "active_level": "high",
                    }
                ],
                "interfaces": [],
                "transactions": [
                    {
                        "order": 1,
                        "bus": "REQ",
                        "producer": "Requester",
                        "consumer": "Responder",
                        "operation": "request",
                        "target": "control register",
                        "payload_or_purpose": "start transfer",
                    }
                ],
                "timing_constraints": [
                    {
                        "name": "ack latency",
                        "start_event": "REQ asserted",
                        "end_event": "ACK asserted",
                        "max_latency": "64 ns",
                        "evidence_text": "REQ to ACK within 64ns",
                    }
                ],
                "phases": [],
                "assumptions": [
                    {
                        "assumption": "REQ high means request is active",
                        "basis": "signal naming convention",
                        "confidence": 0.75,
                    }
                ],
                "uncertain_items": [],
            },
        }

        block = inject.render_block(
            data,
            "vlm-v2",
            max_summary=1200,
            max_keywords=10,
            include_ocr=False,
            max_ocr=500,
        )

        self.assertIn("<!-- figure-enrich: source=vlm-v2 image_id=fig_demo -->", block)
        self.assertIn("### Signals (1)", block)
        self.assertIn("### Transactions (1)", block)
        self.assertIn("### Timing Constraints (1)", block)
        self.assertIn("### Assumptions (1)", block)
        self.assertIn("REQ", block)
        self.assertIn("64 ns", block)
        self.assertIn("0.75", block)

    def test_default_source_tag_is_v2(self) -> None:
        parser = inject.build_arg_parser()
        args = parser.parse_args(
            [
                "--md",
                "dummy.md",
                "--merged-json",
                "dummy.json",
                "--output",
                "out.md",
            ]
        )
        self.assertEqual(args.source_tag, "vlm-v2")


if __name__ == "__main__":
    unittest.main()
