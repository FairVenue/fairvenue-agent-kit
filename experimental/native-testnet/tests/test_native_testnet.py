"""Synthetic native HTTP/continuity fixtures; no funded/live/proof acceptance."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fairvenue_schema6 import AgentClient, AgentError, BotContextProvider, NativeTestnetClient
from fairvenue_schema6 import native_testnet as native
from fairvenue_schema6.context import _scope
from fairvenue_schema6.protocol import NOT_PLACED, AdmitRefusal, grant_copy, jcs, policy_copy
from fairvenue_schema6.transport import HttpTransport
from test_context import ContextFixture

API="https://native-testnet.example/"


def not_placed(request,reason,**changes):
    """The server's exact definitive refusal body for `request`."""
    result={"error":"not_placed","reason":reason,
            "request_sha256":"0x"+hashlib.sha256(jcs(request).encode()).hexdigest(),
            "retry":NOT_PLACED[reason],"proof_backed":False,"l1_finality_verified":False,
            "public_release_go":False,**changes}
    return json.dumps(result,separators=(",",":")).encode()


def refusal_status(reason):
    return 429 if reason=="rate_limited" else 409


class NativeFixture(ContextFixture):
    def __init__(self):
        super().__init__()
        address=list(bytes.fromhex(native._NATIVE_SETTLEMENT[2:]))
        self.policy["domain"]["settlement"]=address
        self.grant["settlement"]=native._NATIVE_SETTLEMENT
        self.context["prefix"]["domain"]["settlement"]=address
        self.context["session_scope"]=_scope(policy_copy(self.policy,self.crypto),
                                            grant_copy(self.grant,policy_copy(self.policy,self.crypto),self.crypto))
        self.recovery_status="accepted"
        self.recovery_edit=None
        self.recovery_calls=0
        self.admit_pause=None
        self.refusal=None
        self.refused=0

    async def post(self,path,body,timeout):
        if path=="/v1/agent/admit" and self.refusal:
            # The server refused before any durable effect: nothing admitted.
            self.refused+=1
            status,reply=self.refusal(json.loads(body))
            if status is None:
                raise AgentError("unavailable")  # e.g. a 503 or transport error
            raise AdmitRefusal(status,reply)
        if path=="/v1/agent/recovery":
            self.recovery_calls+=1
            request=json.loads(body)
            assert set(request)=={"signed_request"}
            original=request["signed_request"]
            context={**copy.deepcopy(self.context),"prefix":original["intent"]["expected"]}
            expected=self.build({"nonce":original["intent"]["nonce"],
                                 "expiresAtMs":original["intent"]["expires_at_ms"],
                                 "action":original["intent"]["action"]},context)
            for signature,message in (("intent_signature","intent_signing_bytes"),
                                      ("ordinary_signature","ordinary_signing_hash"),
                                      ("generation_signature","generation_signing_bytes")):
                assert self.crypto.verify(original[signature],bytes.fromhex(expected[message][2:]),self.signer.public_key)
            result={"schema":"fairvenue-v1-agent-recovery-v1","status":self.recovery_status,
                    "request_sha256":"0x"+hashlib.sha256(jcs(original).encode()).hexdigest(),
                    "step":original["intent"]["expected"]["head"]["count"],"retry_authorized":False,
                    "proof_backed":False,"l1_finality_verified":False,"public_release_go":False}
            if self.recovery_edit:
                self.recovery_edit(result)
            return json.dumps(result,separators=(",",":")).encode()
        if path=="/v1/agent/admit" and self.admit_pause:
            await self.admit_pause()
        try:
            return await super().post(path,body,timeout)
        finally:
            if path=="/v1/agent/admit" and self.context["prefix"]["head"]["count"]:
                self.next_nonce=str(int(json.loads(body)["intent"]["nonce"])+1)

    def client(self,directory,initialize=True):
        return NativeTestnetClient(policy=self.policy,grant=self.grant,signer=self.signer,
                                   transport=self,approved_api_url=API,
                                   custody_path=str(Path(directory).resolve()/"original.sqlite"),initialize=initialize)


class NativeTestnetTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_place_uses_real_three_signatures_and_exact_v1_agent_routes(self):
        fixture = NativeFixture()
        fixture.policy["market_id"] = list(bytes.fromhex(fixture.crypto.digest(b"FairVenue.v2.market-id.v1\0BTCUSDC")[2:]))
        pinned = policy_copy(fixture.policy, fixture.crypto)
        fixture.context["session_scope"] = _scope(pinned, grant_copy(fixture.grant, pinned, fixture.crypto))
        action = {"type": "place", "order": {"symbol": "BTCUSDC", "side": "BUY", "order_type": "LIMIT", "time_in_force": "IOC", "price": "60000.00", "quantity": "0.001", "reduce_only": False, "post_only": False}}
        with tempfile.TemporaryDirectory(prefix="native-order-codec-") as directory:
            # The published golden uses this explicitly public synthetic seed.
            # It is never read from an installed user's credential or signer.
            exported = {"schema": "fairvenue-native-testnet-agent-v1", "environment": "TESTNET", "settlement_mode": "mock_token_sp1_operator_beta", "chain_id": 421614, "settlement": native._NATIVE_SETTLEMENT, "mock_token": native._NATIVE_MOCK_TOKEN, "api_namespace": "/v1", "api_url": API, "agent_policy": fixture.policy, "public_grant": fixture.grant, "private_key_seed": "0x" + "42" * 32}
            key_path = Path(directory).resolve() / "downloaded-synthetic-key.json"
            key_path.write_text(json.dumps(exported))
            key_path.chmod(0o600)
            with patch.object(native, "HttpTransport", return_value=fixture):
                client = NativeTestnetClient.from_credentials(str(key_path), policy=fixture.policy, approved_api_url=API, custody_path=str(Path(directory).resolve() / "original.sqlite"), initialize=True)
            try:
                result = await client.run({**fixture.input, "action": action})
                self.assertEqual(result["recovery"]["status"], "accepted")
                self.assertEqual(fixture.calls["prepare"], 1)
                self.assertEqual(fixture.calls["admit"], 1)
                self.assertEqual(fixture.recovery_calls, 1)
                self.assertFalse(client.has_pending_request())
            finally:
                client.close()

    async def test_explicit_native_context_three_signatures_and_original_only_completion(self):
        fixture=NativeFixture()
        with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
            client=fixture.client(directory)
            try:
                snapshot=await client.snapshot()
                self.assertEqual(snapshot["next_nonce"],"1")
                snapshot["context"]["owner"]="foreign"
                result=await client.run(fixture.input)
                self.assertEqual(result["ack"]["outcome"],"trade")
                self.assertEqual(result["recovery"]["status"],"accepted")
                self.assertFalse(client.has_pending_request())
                self.assertEqual(fixture.calls["admit"],1)
                self.assertEqual(fixture.recovery_calls,1)
                self.assertNotIn("private_key",Path(directory,"original.sqlite").read_bytes().decode("latin1"))
                with self.assertRaises(AgentError) as caught:
                    await client.run(fixture.input)
                self.assertEqual(caught.exception.code,"nonce_reused")
            finally:
                client.close()

    async def test_lost_ack_retained_across_restart_recovery_never_resends(self):
        fixture=NativeFixture();fixture.lost_reply=True
        with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
            client=fixture.client(directory)
            with self.assertRaises(AgentError) as caught:
                await client.run(fixture.input)
            self.assertEqual(caught.exception.code,"outcome_unknown")
            self.assertTrue(client.has_pending_request())
            retained=client._custody.read()[2]
            self.assertEqual(set(retained),{"schema","request","protocol_time_ms"})
            self.assertNotIn("session_scope",json.dumps(retained))
            client.close()
            restored=fixture.client(directory,False)
            try:
                self.assertEqual(restored._custody.read()[2],retained)
                result=await restored.recover_original()
                self.assertEqual(result["status"],"accepted")
                self.assertFalse(restored.has_pending_request())
                self.assertEqual(fixture.calls["admit"],1)
            finally:
                restored.close()

    async def test_unresolved_wrong_hash_wrong_step_or_proof_cannot_clear_original(self):
        edits=(lambda v:v.update(request_sha256="0x"+"1"*64),lambda v:v.update(step=9),
               lambda v:v.update(retry_authorized=True),lambda v:v.update(proof_backed=True),
               lambda v:v.update(status="final"),lambda v:v.update(private_key="forbidden"))
        for edit in (None,*edits):
            fixture=NativeFixture();fixture.lost_reply=True
            with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
                client=fixture.client(directory)
                try:
                    with self.assertRaises(AgentError):
                        await client.run(fixture.input)
                    original=copy.deepcopy(client._custody.read()[2])
                    fixture.recovery_status="unresolved";fixture.recovery_edit=edit
                    if edit:
                        with self.assertRaises(AgentError):
                            await client.recover_original()
                    else:
                        self.assertEqual((await client.recover_original())["status"],"unresolved")
                    self.assertEqual(client._custody.read()[2],original)
                    with self.assertRaises(AgentError):
                        await client.run({**fixture.input,"nonce":"2"})
                    self.assertEqual(fixture.calls["admit"],1)
                finally:
                    client.close()

    async def test_terminal_not_admitted_or_voided_clears_original_and_frees_its_nonce(self):
        def voided(opening,**changes):
            batch={"batch":opening["batch"],"generation":opening["generation"],
                   "replacement_generation":opening["generation"]+1,
                   "replacement_transaction":"0x"+"5"*64,"reason":"dead","settled":False,"re_executed":False}
            return lambda v:v.update(settlement="not_settled",voided_batch={**batch,**changes})
        # `opening_failed` names the failed original when one was signed, else
        # an explicit null; `dead` always names the adopted replacement.
        for status,changes in (("not_admitted",None),("not_settled",{}),
                               ("not_settled",{"reason":"opening_failed"}),
                               ("not_settled",{"reason":"opening_failed","replacement_transaction":None})):
            fixture=NativeFixture();fixture.lost_reply=True
            head=copy.deepcopy(fixture.context["prefix"]["head"])
            with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
                client=fixture.client(directory)
                try:
                    with self.assertRaises(AgentError):
                        await client.run(fixture.input)
                    # The server never admitted it (or voided it): no native effect.
                    fixture.context["prefix"]["head"]=head;fixture.next_nonce="1"
                    opening=client._custody.read()[2]["request"]["intent"]["expected"]["opening"]
                    fixture.recovery_status=status
                    fixture.recovery_edit=voided(opening,**changes) if status=="not_settled" else None
                    self.assertEqual((await client.recover_original())["status"],status)
                    self.assertFalse(client.has_pending_request())
                    self.assertEqual(client._custody.read()[1],0)
                    # Nothing was consumed natively: the same nonce is signed afresh.
                    fixture.recovery_status="accepted";fixture.recovery_edit=None
                    self.assertEqual((await client.run(fixture.input))["recovery"]["status"],"accepted")
                    self.assertEqual(fixture.calls["admit"],2)
                finally:
                    client.close()

    async def test_foreign_or_settled_voided_binding_cannot_clear_original(self):
        for changes in ({"batch":9},{"generation":9},{"replacement_generation":0},{"settled":True},
                        {"re_executed":True},{"replacement_transaction":"0x"+"0"*64},
                        {"replacement_transaction":"0x5"},
                        # An adopted replacement always names its transaction.
                        {"replacement_transaction":None},
                        {"reason":"wrong_root","replacement_transaction":None},
                        # `opening_failed`: a nonzero hash or null, nothing else.
                        {"reason":"opening_failed","replacement_transaction":"0x"+"0"*64},
                        {"reason":"opening_failed","replacement_transaction":0},
                        # Empty voids are `not_admitted`, never a voided binding.
                        {"reason":"expired_empty","replacement_transaction":None},
                        {"reason":"dropped_empty"},{"reason":"operator"}):
            fixture=NativeFixture();fixture.lost_reply=True
            with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
                client=fixture.client(directory)
                try:
                    with self.assertRaises(AgentError):
                        await client.run(fixture.input)
                    original=copy.deepcopy(client._custody.read()[2])
                    opening=original["request"]["intent"]["expected"]["opening"]
                    batch={"batch":opening["batch"],"generation":opening["generation"],
                           "replacement_generation":opening["generation"]+1,
                           "replacement_transaction":"0x"+"5"*64,"reason":"dead","settled":False,"re_executed":False}
                    fixture.recovery_status="not_settled"
                    fixture.recovery_edit=lambda v:v.update(settlement="not_settled",voided_batch={**batch,**changes})
                    with self.assertRaises(AgentError):
                        await client.recover_original()
                    self.assertEqual(client._custody.read()[2],original)
                    # A voided binding never accompanies another status.
                    fixture.recovery_status="not_admitted"
                    fixture.recovery_edit=lambda v:v.update(settlement="not_settled",voided_batch=batch)
                    with self.assertRaises(AgentError):
                        await client.recover_original()
                    self.assertEqual(client._custody.read()[2],original)
                finally:
                    client.close()

    async def test_voided_binding_numbers_are_exact_integers_before_any_nonce_reset(self):
        # Values that merely compare equal in Python (bool, float) are not the
        # Rust/browser integers; the retained original, its custody `highest`
        # and the in-memory nonce floor stay untouched until the exact binding
        # validated, and only then roll back with the voided lineage.
        def lookalike(opening):
            yield {"batch":float(opening["batch"])}
            yield {"generation":float(opening["generation"])}
            yield {"replacement_generation":float(opening["generation"]+1)}
            yield {"replacement_generation":True}
            yield {"batch":str(opening["batch"])}
            yield {"reason":["dead"]}
            yield {"replacement_transaction":["0x"+"5"*64]}
            for key in ("batch","generation"):
                if opening[key] in (0,1):
                    yield {key:bool(opening[key])}
        fixture=NativeFixture();fixture.lost_reply=True
        with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
            client=fixture.client(directory)
            try:
                with self.assertRaises(AgentError):
                    await client.run(fixture.input)
                revision,_,original=client._custody.read()
                client._custody.change(revision,5,original)
                client._nonce_floor=7
                opening=original["request"]["intent"]["expected"]["opening"]
                batch={"batch":opening["batch"],"generation":opening["generation"],
                       "replacement_generation":opening["generation"]+1,
                       "replacement_transaction":"0x"+"5"*64,"reason":"dead","settled":False,"re_executed":False}
                fixture.recovery_status="not_settled"
                for changes in lookalike(opening):
                    fixture.recovery_edit=lambda v,c=changes:v.update(settlement="not_settled",voided_batch={**batch,**c})
                    with self.assertRaises(AgentError):
                        await client.recover_original()
                    self.assertEqual(client._custody.read()[1:],(5,original))
                    self.assertEqual(client._nonce_floor,7)
                fixture.recovery_edit=lambda v:v.update(settlement="not_settled",voided_batch=batch)
                self.assertEqual((await client.recover_original())["status"],"not_settled")
                self.assertEqual(client._custody.read()[1:],(0,None))
                self.assertEqual(client._nonce_floor,0)
            finally:
                client.close()

    async def test_tampered_prepare_context_and_grant_refuse_before_dispatch(self):
        for edit in (lambda f:setattr(f,"prepare_edit",lambda v:v.update(ordinary_signing_hash="0x"+"1"*64)),
                     lambda f:setattr(f,"edit",lambda v:v.update(challenge="0x"+"1"*64)),
                     lambda f:setattr(f,"edit",lambda v:v["public_grant"].update(canWithdraw=True))):
            fixture=NativeFixture();edit(fixture)
            with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
                client=fixture.client(directory)
                try:
                    with self.assertRaises(AgentError):
                        await client.run(fixture.input)
                    self.assertFalse(client.has_pending_request())
                    self.assertEqual(fixture.calls["admit"],0)
                finally:
                    client.close()

    async def test_definitive_not_placed_clears_original_without_consuming_its_nonce(self):
        for reason in NOT_PLACED:
            with self.subTest(reason=reason):
                fixture=NativeFixture()
                fixture.refusal=lambda request,r=reason:(refusal_status(r),not_placed(request,r))
                with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
                    client=fixture.client(directory)
                    try:
                        with self.assertRaises(AgentError) as caught:
                            await client.run(fixture.input)
                        self.assertEqual(caught.exception.code,reason)
                        self.assertFalse(client.has_pending_request())
                        # Not consumed: `highest` stays, the same nonce signs afresh.
                        self.assertEqual(client._custody.read()[1:],(0,None))
                        self.assertEqual((fixture.refused,fixture.calls["admit"],fixture.recovery_calls),(1,0,0))
                        fixture.refusal=None
                        self.assertEqual((await client.run(fixture.input))["recovery"]["status"],"accepted")
                        self.assertEqual(fixture.calls["admit"],1)
                    finally:
                        client.close()

    async def test_mismatched_malformed_or_generic_refusal_retains_original(self):
        generic=json.dumps({"error":"operator beta participant request unavailable or invalid",
                            "proof_backed":False,"public_release_go":False}).encode()
        cases=(lambda q:(409,not_placed(q,"batch_closed",request_sha256="0x"+"1"*64)),
               lambda q:(409,not_placed({**q,"intent_signature":"0x"+"2"*128},"batch_closed")),
               lambda q:(409,not_placed(q,"batch_closed",
                                        request_sha256="0x"+hashlib.sha256(jcs(q).encode()).hexdigest().upper())),
               lambda q:(409,not_placed(q,"batch_closed",extra=True)),
               lambda q:(409,not_placed(q,"batch_closed",retry="later")),
               lambda q:(409,not_placed(q,"admission_closed",retry="resign")),
               lambda q:(409,not_placed(q,"rate_limited")),
               lambda q:(429,not_placed(q,"batch_closed")),
               lambda q:(409,not_placed(q,"slot_moved",retry="resign")),
               lambda q:(409,not_placed(q,"batch_closed",error="outcome_unknown")),
               lambda q:(409,not_placed(q,"batch_closed",proof_backed=True)),
               lambda q:(409,not_placed(q,"batch_closed",l1_finality_verified=0)),
               lambda q:(409,b"not json"),
               lambda q:(409,generic),lambda q:(429,generic),
               lambda q:(None,None))  # 503, timeout or transport failure
        for index,case in enumerate(cases):
            with self.subTest(case=index):
                fixture=NativeFixture();fixture.refusal=case
                with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
                    client=fixture.client(directory)
                    try:
                        with self.assertRaises(AgentError) as caught:
                            await client.run(fixture.input)
                        self.assertEqual(caught.exception.code,"outcome_unknown")
                        self.assertTrue(client.has_pending_request())
                        retained=client._custody.read()[2]
                        self.assertEqual(retained["request"]["intent"]["nonce"],fixture.input["nonce"])
                        self.assertEqual(fixture.recovery_calls,0)
                        # Retained: no new action until recover_original resolves it.
                        with self.assertRaises(AgentError) as again:
                            await client.run({**fixture.input,"nonce":"2"})
                        self.assertEqual(again.exception.code,"outcome_unknown")
                        self.assertEqual(client._custody.read()[2],retained)
                    finally:
                        client.close()

    async def test_transport_surfaces_only_admit_409_429_json_bodies(self):
        import http.server
        import threading
        replies={}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                status,kind,body=replies[self.path]
                self.send_response(status)
                self.send_header("Content-Type",kind)
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self,*_):
                pass
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            transport=HttpTransport(f"http://127.0.0.1:{server.server_address[1]}")
            body=b'{"error":"not_placed"}'
            for status in (409,429):
                replies["/v1/agent/admit"]=(status,"application/json",body)
                with self.assertRaises(AdmitRefusal) as caught:
                    await transport.post("/v1/agent/admit",b"{}",5)
                self.assertEqual((caught.exception.status,caught.exception.body,caught.exception.code),
                                 (status,body,"outcome_unknown"))
            for path,reply in (("/v1/agent/admit",(503,"application/json",body)),
                               ("/v1/agent/admit",(409,"text/plain",body)),
                               ("/v1/agent/admit",(400,"application/json",body)),
                               ("/v1/agent/prepare",(409,"application/json",body))):
                replies[path]=reply
                with self.assertRaises(AgentError) as caught:
                    await transport.post(path,b"{}",5)
                self.assertNotIsInstance(caught.exception,AdmitRefusal)
                self.assertEqual(caught.exception.code,"unavailable")
            replies["/v1/agent/admit"]=(200,"application/json",b'{"ok":true}')
            self.assertEqual(await transport.post("/v1/agent/admit",b"{}",5),b'{"ok":true}')
        finally:
            await asyncio.to_thread(server.shutdown);server.server_close()

    async def test_timeout_keeps_live_callback_fence_and_public_original(self):
        fixture=NativeFixture()
        started,release=asyncio.Event(),asyncio.Event()
        async def pause():
            started.set();await release.wait()
        fixture.admit_pause=pause
        with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
            client=fixture.client(directory)
            with self.assertRaises(AgentError):
                await client.run(fixture.input,timeout_ms=100)
            self.assertTrue(started.is_set())
            self.assertTrue(client.has_pending_request())
            with self.assertRaises(AgentError) as caught:
                await client.recover_original()
            self.assertEqual(caught.exception.code,"busy")
            with self.assertRaises(AgentError):
                client.close()
            release.set();await asyncio.sleep(0.02)
            self.assertEqual((await client.recover_original())["status"],"accepted")
            client.close()

    async def test_existing_strict_factories_stay_closed_before_io(self):
        fixture=NativeFixture()
        strict=AgentClient(policy=fixture.policy,grant=fixture.grant,signer=fixture.signer,
                           current_context=fixture.current_context,transport=fixture)
        context=BotContextProvider(policy=fixture.policy,grant=fixture.grant,signer=fixture.signer,transport=fixture)
        for callback in (lambda:strict.run(fixture.input),strict.retry_admission,context.snapshot):
            with self.assertRaises(AgentError) as caught:
                await callback()
            self.assertEqual(caught.exception.code,"activation_closed")
        self.assertEqual(fixture.signed,[])


class NativeCustodyTests(unittest.TestCase):
    def test_actual_downloaded_testnet_loader_constructs_without_http_or_private_export(self):
        from test_native_credentials import fixture, write
        policy, exported=fixture()
        with tempfile.TemporaryDirectory(prefix="native-testnet-key-") as key_directory, tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
            key_path=write(key_directory,exported)
            with patch("fairvenue_schema6.transport._http",side_effect=AssertionError("no I/O on construction")):
                client=NativeTestnetClient.from_credentials(key_path,policy=policy,approved_api_url=API,
                        custody_path=str(Path(directory).resolve()/"original.sqlite"),initialize=True)
            try:
                self.assertFalse(client.has_pending_request())
                self.assertNotIn(exported["private_key_seed"],repr(client))
                self.assertNotIn(exported["private_key_seed"].encode(),Path(directory,"original.sqlite").read_bytes())
                self.assertEqual(Path(key_path).read_bytes(),json.dumps(exported,separators=(",",":")).encode())
            finally:
                client.close()

    def test_private_existing_config_lock_and_unknown_namespace_no_reset(self):
        fixture=NativeFixture()
        with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
            client=fixture.client(directory)
            with self.assertRaises(AgentError):
                fixture.client(directory,True)
            with self.assertRaises(AgentError):
                fixture.client(directory,False)
            client.close()
            path=Path(directory,"original.sqlite")
            before=path.read_bytes()
            os.chmod(path,0o644)
            with self.assertRaises(AgentError):
                fixture.client(directory,False)
            self.assertEqual(path.read_bytes(),before)
            os.chmod(path,0o600)
            connection=sqlite3.connect(path)
            connection.execute("UPDATE original SET config=?",(b"x"*32,));connection.commit();connection.close()
            foreign=path.read_bytes()
            with self.assertRaises(AgentError):
                fixture.client(directory,False)
            self.assertEqual(path.read_bytes(),foreign)
        with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as missing:
            with self.assertRaises(AgentError):
                fixture.client(missing,False)
            self.assertFalse(Path(missing,"original.sqlite").exists())

    def test_production_wrong_deployment_or_unapproved_url_refuse_without_io(self):
        for edit in (lambda f:f.policy["domain"].update(chain_id=1),
                     lambda f:f.policy["domain"].update(settlement=[9]*20),
                     lambda f:f.grant.update(canWithdraw=True)):
            fixture=NativeFixture();edit(fixture)
            with tempfile.TemporaryDirectory(prefix="native-testnet-fixture-") as directory:
                with self.assertRaises(AgentError):
                    fixture.client(directory)
                self.assertFalse(Path(directory,"original.sqlite").exists())
        with self.assertRaises(AgentError):
            NativeTestnetClient.from_credentials("/not/a/key",policy={},approved_api_url="http://bad.example/",
                                                custody_path="/unused")
