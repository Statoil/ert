import asyncio
from asyncio.events import AbstractEventLoop
from asyncio.queues import QueueEmpty
from datetime import date
from enum import Enum, auto
import re
from typing import Any, Dict, Iterator, List, Optional, Union
import uuid
import threading

from cloudevents.sdk import types
from ert_shared.ensemble_evaluator.ws_util import wait
from websockets.server import WebSocketServer

from fnmatch import fnmatchcase

try:
    from typing import TypedDict  # >=3.8
except ImportError:
    from mypy_extensions import TypedDict  # <=3.7

import websockets
from cloudevents.http import CloudEvent, to_json, from_json


class _ConnectionInformation(TypedDict):  # type: ignore
    uri: str
    proto: str
    hostname: str
    port: int
    path: str
    base_uri: str

    @classmethod
    def from_uri(cls, uri: str):
        proto, hostname, port = uri.split(":")
        path = ""
        if "/" in port:
            port, path = port.split("/")
        hostname = hostname[2:]
        path = "/" + path
        port = int(port)
        hostname
        base_uri = f"{proto}://{hostname}:{port}"
        return cls(
            uri=uri,
            proto=proto,
            hostname=hostname,
            port=port,
            path=path,
            base_uri=base_uri,
        )


class ReMatch:
    def __init__(self, regex: re.Pattern, replace_with: str) -> None:
        self.regex = regex
        self.replace_with = replace_with


class EventDescription(TypedDict):  # type: ignore
    id_: str
    source: Union[str, ReMatch]
    type_: Union[str, ReMatch]
    datacontenttype: Optional[Union[str, ReMatch]]
    subject: Optional[Union[str, ReMatch]]
    data: Optional[Any]


class _Event:
    def __init__(self, description: EventDescription) -> None:
        self._id = description.get("id_", uuid.uuid4())
        self.source = description["source"]
        self.type_ = description["type_"]
        self.datacontenttype = description.get("datacontenttype")
        self.subject = description.get("subject")
        self.data = description.get("data")

    def __repr__(self) -> str:
        s = "Event("
        for attr in [
            (self.source, "Source"),
            (self.type_, "Type"),
            (self.datacontenttype, "Datacontenttype"),
            (self.subject, "Subject"),
            (self.data, "Data"),
        ]:
            if isinstance(attr[0], ReMatch):
                s += f"{attr[1]}: {attr[0].regex} "
            elif attr[0]:
                s += f"{attr[1]}: {attr[0]} "
        s += f"Id: {self._id})"
        return s

    def dict_match(self, original, match):
        for k, v in match.items():
            assert k in original
            if isinstance(v, dict):
                assert isinstance(original[k], dict)
                self.dict_match(original[k], v)
            elif isinstance(v, ReMatch):
                assert isinstance(original[k], str)
                assert v.regex.match(original[k])
            else:
                assert original[k] == v

    def assert_matches(self, other: CloudEvent):
        msg_tmpl = "{self} did not match {other}: {reason}"

        if self.data:
            self.dict_match(other.data, self.data)
            # for k, v in self.data.items():
            #     assert k in other.data, msg_tmpl.format(
            #         self=self, other=other, reason=f"{k} not in {other.data}"
            #     )
            #     if isinstance(v, ReMatch):
            #         assert v.regex.match(other.data[k]), msg_tmpl.format(
            #             self=self, other=other, reason=f"no match for {v} in {k}"
            #         )
            #     else:
            #         assert v == other.data[k], msg_tmpl.format(
            #             self=self, other=other, reason=f"{v} != {other.data[k]} for {k}"
            #         )

        for attr in filter(
            lambda x: x[0] is not None,
            [
                (self.source, "source"),
                (self.type_, "type"),
                (self.subject, "subject"),
                (self.datacontenttype, "datacontenttype"),
            ],
        ):
            if isinstance(attr[0], ReMatch):
                assert attr[0].regex.match(other[attr[1]]), msg_tmpl.format(
                    self=self,
                    other=other,
                    reason=f"no match for {attr[0]} in {attr[1]}",
                )
            else:
                assert attr[0] == other[attr[1]], msg_tmpl.format(
                    self=self, other=other, reason=f"{attr[0]} != {other[attr[1]]}"
                )

    def to_cloudevent(self) -> CloudEvent:
        attrs = {}
        for attr in [
            (self.source, "source"),
            (self.type_, "type"),
            (self.subject, "subject"),
            (self.datacontenttype, "datacontenttype"),
        ]:
            if isinstance(attr[0], ReMatch):
                attrs[attr[1]] = attr[0].replace_with
            else:
                attrs[attr[1]] = attr[0]
        data = {}
        if self.data:
            for k, v in self.data.items():
                if isinstance(v, ReMatch):
                    data[k] = v.replace_with
                else:
                    data[k] = v
        return CloudEvent(attrs, data)


class _Interaction:
    def __init__(self, provider_states: Optional[List[Dict[str, Any]]]) -> None:
        self.provider_states: Optional[List[Dict[str, Any]]] = provider_states
        self.scenario: str = ""
        self.events: List[_Event] = []

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(Scenario: {self.scenario})"

    def assert_matches(self, event: _Event, other: CloudEvent):
        event.assert_matches(other)


class _RecurringInteraction(_Interaction):
    def __init__(
        self, provider_states: Optional[List[Dict[str, Any]]], terminator: _Event
    ) -> None:
        super().__init__(provider_states)
        self.terminator = terminator

    def assert_matches(self, other: CloudEvent):
        try:
            self.terminator.assert_matches(other)
        except AssertionError:
            pass
        else:
            raise _InteractionTermination()

        for ev in self.events:
            try:
                ev.assert_matches(other)
            except AssertionError:
                continue
            else:
                return
        raise AssertionError(f"No event in {self} matched {other}")


class _Request(_Interaction):
    pass


class _RecurringRequest(_RecurringInteraction):
    pass


class _Response(_Interaction):
    pass


class _RecurringResponse(_RecurringInteraction):
    pass


class _InteractionTermination(Exception):
    pass


class _ProviderVerifier:
    def __init__(
        self,
        interactions: List[_Interaction],
        uri: str,
        unmarshaller: Optional[types.UnmarshallerType],
        marshaller: Optional[types.MarshallerType],
    ) -> None:
        self._interactions: List[_Interaction] = interactions
        self._uri = uri
        self._unmarshaller = unmarshaller
        self._marshaller = marshaller

        # A queue on which errors will be put
        self._errors: asyncio.Queue = asyncio.Queue()

    def verify(self, on_connect):
        self._ws_thread = threading.Thread(
            target=self._sync_listener, args=[on_connect]
        )
        self._ws_thread.start()
        if asyncio.get_event_loop().is_running():
            raise RuntimeError(
                "sync narrative should control the loop, maybe you called it from within an async test?"
            )
        self._ws_thread.join()
        errors = asyncio.get_event_loop().run_until_complete(self._verify())
        if errors:
            raise AssertionError(errors)

    async def _mock_listener(self, on_connect):
        async with websockets.connect(self._uri) as websocket:
            on_connect()
            for interaction in self._interactions:
                if type(interaction) == _Interaction:
                    e = TypeError(
                        "the first interaction needs to be promoted to either response or receive"
                    )
                    self._errors.put_nowait(e)
                elif isinstance(interaction, _Request):
                    for event in interaction.events:
                        await websocket.send(to_json(event.to_cloudevent()))
                    print("OK", interaction.scenario)
                elif isinstance(interaction, _Response):
                    for event in interaction.events:
                        received_event = await websocket.recv()
                        try:
                            interaction.assert_matches(
                                event,
                                from_json(
                                    received_event, data_unmarshaller=self._unmarshaller
                                ),
                            )
                        except AssertionError as e:
                            self._errors.put_nowait(e)
                    print("OK", interaction.scenario)
                elif isinstance(interaction, _RecurringResponse):
                    event_counter = 0
                    while True:
                        received_event = await websocket.recv()
                        event_counter += 1
                        try:
                            interaction.assert_matches(
                                from_json(
                                    received_event, data_unmarshaller=self._unmarshaller
                                )
                            )
                        except _InteractionTermination:
                            break
                        except AssertionError as e:
                            self._errors.put_nowait(e)
                            break
                    print(f"OK ({event_counter} events)", interaction.scenario)
                elif isinstance(interaction, _RecurringRequest):
                    raise TypeError("don't know how to request recurringly")
                else:
                    e = TypeError(
                        f"expected either receive or response, got {interaction}"
                    )
                    self._errors.put_nowait(e)

    def _sync_listener(self, on_connect):
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._mock_listener(on_connect))
        self._loop.close()

    async def _verify(self):
        errors: List[Exception] = []
        while True:
            try:
                errors.append(self._errors.get_nowait())
            except QueueEmpty:
                break
        return errors


class _ProviderMock:
    def __init__(
        self,
        interactions: List[_Interaction],
        conn_info: _ConnectionInformation,
        unmarshaller: Optional[types.UnmarshallerType],
        marshaller: Optional[types.MarshallerType],
    ) -> None:
        self._interactions: List[_Interaction] = interactions
        self._loop: Optional[AbstractEventLoop] = None
        self._ws: Optional[WebSocketServer] = None
        self._conn_info = conn_info
        self._unmarshaller = unmarshaller
        self._marshaller = marshaller

        # A queue on which errors will be put
        self._errors: asyncio.Queue = asyncio.Queue()

    @property
    def uri(self) -> str:
        return self._conn_info["uri"]

    @property
    def hostname(self) -> str:
        return self._conn_info["hostname"]

    @property
    def port(self) -> str:
        return self._conn_info["port"]

    async def _mock_handler(self, websocket, path):
        expected_path = self._conn_info["path"]
        if path != expected_path:
            print(f"not handling {path} as it is not the expected path {expected_path}")
            return
        for interaction in self._interactions:
            if type(interaction) == _Interaction:
                e = TypeError(
                    "the first interaction needs to be promoted to either response or receive"
                )
                self._errors.put_nowait(e)
            elif isinstance(interaction, _Request):
                for event in interaction.events:
                    received_event = await websocket.recv()
                    try:
                        interaction.assert_matches(
                            event,
                            from_json(
                                received_event, data_unmarshaller=self._unmarshaller
                            ),
                        )
                    except AssertionError as e:
                        self._errors.put_nowait(e)
                print("OK", interaction.scenario)
            elif isinstance(interaction, _Response):
                for event in interaction.events:
                    await websocket.send(
                        to_json(event.to_cloudevent(), data_marshaller=self._marshaller)
                    )
                print("OK", interaction.scenario)
            elif isinstance(interaction, _RecurringResponse):
                for event in interaction.events:
                    await websocket.send(
                        to_json(event.to_cloudevent(), data_marshaller=self._marshaller)
                    )
                await websocket.send(
                    to_json(
                        interaction.terminator.to_cloudevent(),
                        data_marshaller=self._marshaller,
                    )
                )
                print("OK", interaction.scenario)
            elif isinstance(interaction, _RecurringRequest):
                event_counter = 0
                while True:
                    received_event = await websocket.recv()
                    event_counter += 1
                    try:
                        interaction.assert_matches(received_event)
                    except _InteractionTermination:
                        break
                    except AssertionError as e:
                        self._errors.put_nowait(e)
                        break
                print(f"OK ({event_counter} events)", interaction.scenario)
            else:
                e = TypeError(f"expected either receive or response, got {interaction}")
                self._errors.put_nowait(e)

    def _sync_ws(self, delay_startup=0):
        self._loop = asyncio.new_event_loop()
        self._done = self._loop.create_future()

        async def _serve():
            await asyncio.sleep(delay_startup)
            ws = await websockets.serve(
                self._mock_handler, self._conn_info["hostname"], self._conn_info["port"]
            )
            await self._done
            ws.close()
            await ws.wait_closed()

        self._loop.run_until_complete(_serve())
        self._loop.close()

    async def _verify(self):
        errors = []
        while True:
            try:
                errors.append(self._errors.get_nowait())
            except QueueEmpty:
                break
        return errors

    def __enter__(self):
        self._ws_thread = threading.Thread(target=self._sync_ws)
        self._ws_thread.start()
        if asyncio.get_event_loop().is_running():
            raise RuntimeError(
                "sync narrative should control the loop, maybe you called it from within an async test?"
            )
        asyncio.get_event_loop().run_until_complete(
            wait(self._conn_info["base_uri"], 2)
        )
        return self

    def __exit__(self, *args, **kwargs):
        self._loop.call_soon_threadsafe(self._done.set_result, None)
        self._ws_thread.join()
        errors = asyncio.get_event_loop().run_until_complete(self._verify())
        if errors:
            raise AssertionError(errors)

    async def __aenter__(self):
        self._ws = await websockets.serve(
            self._mock_handler, self._conn_info["hostname"], self._conn_info["port"]
        )
        return self

    async def __aexit__(self, *args):
        self._ws.close()
        await self._ws.wait_closed()
        errors = await self._verify()
        if errors:
            raise AssertionError(errors)


class _Narrative:
    def __init__(self, consumer: "Consumer", provider: "Provider") -> None:
        self.consumer = consumer
        self.provider = provider
        self.interactions: List[_Interaction] = []
        self._mock: Optional[_ProviderMock] = None
        self._conn_info: Optional[_ConnectionInformation] = None
        self._unmarshaller: Optional[types.UnmarshallerType] = None
        self._marshaller: Optional[types.MarshallerType] = None

    def given(self, provider_state: Optional[str], **params) -> "_Narrative":
        state = None
        if provider_state:
            state = [{"name": provider_state, "params": params}]
        self.interactions.append(_Interaction(state))
        return self

    def and_given(self, provider_state: str, **params) -> "_Narrative":
        raise NotImplementedError("not yet implemented")

    def receives(self, scenario: str) -> "_Narrative":
        interaction = self.interactions[-1]
        if type(interaction) == _Interaction:
            interaction.__class__ = _Request
        elif not interaction.events:
            raise ValueError("receive followed an empty response scenario")
        else:
            interaction = _Request(self.interactions[-1].provider_states)
            self.interactions.append(interaction)
        interaction.scenario = scenario
        return self

    def responds_with(self, scenario: str) -> "_Narrative":
        interaction = self.interactions[-1]
        if type(interaction) == _Interaction:
            interaction.__class__ = _Response
        elif (
            isinstance(interaction, (_Request, _RecurringRequest))
            and not interaction.events
        ):
            raise ValueError("response followed an empty request scenario")
        else:
            interaction = _Response(self.interactions[-1].provider_states)
            self.interactions.append(interaction)
        interaction.scenario = scenario
        return self

    def cloudevents_in_order(self, events: List[EventDescription]) -> "_Narrative":
        cloudevents = []
        for event in events:
            cloudevents.append(_Event(event))
        self.interactions[-1].events = cloudevents
        return self

    def repeating_unordered_events(
        self, events: List[EventDescription], terminator=EventDescription
    ) -> "_Narrative":
        events_list = []
        for event in events:
            events_list.append(_Event(event))
        interaction = self.interactions[-1]
        if type(interaction) == _Response:
            self.interactions[-1] = _RecurringResponse(
                interaction.provider_states, _Event(terminator)
            )
        elif isinstance(interaction, _Request):
            self.interactions[-1] = _RecurringRequest(
                interaction.provider_states, _Event(terminator)
            )
        elif isinstance(
            interaction, (_RecurringRequest, _RecurringResponse, _RecurringInteraction)
        ):
            raise TypeError(
                f"interaction {interaction} already recurring, define new interaction"
            )
        else:
            raise ValueError(f"cannot promote {interaction}")
        self.interactions[-1].events = events_list
        self.interactions[-1].scenario = interaction.scenario
        return self

    def on_uri(self, uri: str) -> "_Narrative":
        self._conn_info = _ConnectionInformation.from_uri(uri)
        return self

    def with_unmarshaller(self, data_unmarshaller: types.UnmarshallerType):
        self._unmarshaller = data_unmarshaller
        return self

    def with_marshaller(self, data_marshaller: types.MarshallerType):
        self._marshaller = data_marshaller
        return self

    @property
    def uri(self) -> str:
        if not self._conn_info:
            raise ValueError("no connection information")
        return self._conn_info.get("uri")

    def _reset(self):
        self._conn_info = None
        self._unmarshaller = None
        self._marshaller = None

    def __enter__(self):
        if not self._conn_info:
            raise ValueError("no connection info on mock")
        self._mock = _ProviderMock(
            self.interactions, self._conn_info, self._unmarshaller, self._marshaller
        )
        return self._mock.__enter__()

    def __exit__(self, *args, **kwargs):
        self._mock.__exit__(*args, **kwargs)
        self._reset()

    async def __aenter__(self):
        if not self._conn_info:
            raise ValueError("no connection info on mock")
        self._mock = _ProviderMock(
            self.interactions, self._conn_info, self._unmarshaller, self._marshaller
        )
        return await self._mock.__aenter__()

    async def __aexit__(self, *args):
        await self._mock.__aexit__(*args)
        self._reset()

    def verify(self, provider_uri, on_connect) -> _ProviderVerifier:
        _ProviderVerifier(
            self.interactions, provider_uri, self._unmarshaller, self._marshaller
        ).verify(on_connect)


class _Actor:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


class Provider(_Actor):
    pass


class Consumer(_Actor):
    def forms_narrative_with(self, provider: Provider, **kwargs) -> _Narrative:
        return _Narrative(self, provider, **kwargs)
