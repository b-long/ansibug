# -*- coding: utf-8 -*-
# Copyright (c) 2022 Jordan Borean
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import contextlib
import dataclasses
import functools
import logging
import os
import pathlib
import queue
import threading
import typing as t

from . import dap
from ._mp_queue import ClientMPQueue, MPProtocol, MPQueue, ServerMPQueue
from ._singleton import Singleton
from ._socket_helper import CancelledError, SocketCancellationToken

log = logging.getLogger(__name__)


def get_pid_info_path(pid: int) -> str:
    """Get the path used to store info about the ansible-playbook debug proc."""
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return str(pathlib.Path(tmpdir) / f"ANSIBUG-{pid}")


def wait_for_dap_server(
    addr: t.Tuple[str, int],
    proto_factory: t.Callable[[], MPProtocol],
    mode: t.Literal["connect", "listen"],
    cancel_token: SocketCancellationToken,
) -> MPQueue:
    """Wait for DAP Server.

    Attempts to either connect to a DAP server or start a new socket that the
    DAP server connects to. This connection exposes 2 methods that can send and
    receive DAP messages to and from the DAP server.

    Args:
        addr: The addr of the socket.
        proto_factory:
        mode: The socket mode to use, connect will connect to the addr while
            listen will bind to the addr and wait for a connection.
        cancel_token: The cancellation token to cancel the socket operations.

    Returns:
        MPQueue: The multiprocessing queue handler that can exchange DAP
        messages with the peer.
    """
    log.info("Setting up ansible-playbook debug %s socket at '%s'", mode, addr)

    mp_queue = (ClientMPQueue if mode == "connect" else ServerMPQueue)(addr, proto_factory, cancel_token=cancel_token)
    if isinstance(mp_queue, ServerMPQueue):
        bound_addr = mp_queue.address

        with open(get_pid_info_path(os.getpid()), mode="w") as fd:
            fd.write(f"{bound_addr[0]}:{bound_addr[1]}")

    return mp_queue


class DAProtocol(MPProtocol):
    def __init__(
        self,
        debugger: AnsibleDebugger,
    ) -> None:
        self._debugger = debugger

    def on_msg_received(
        self,
        msg: dap.ProtocolMessage,
    ) -> None:
        log.info("Processing msg %r", msg)
        try:
            self._debugger.process_message(msg)
        except Exception as e:
            log.exception("Exception while processing msg seq %d", msg.seq)

            if isinstance(msg, dap.Request):
                resp = dap.ErrorResponse(
                    command=msg.command,
                    request_seq=msg.seq,
                    message=f"Unknown error: {e!r}",
                    # error=dap.Message(),  # FIXME
                )
                self._debugger.send(resp)

    def connection_closed(
        self,
        exp: t.Optional[Exception],
    ) -> None:
        # FIXME: log exception
        self._debugger.send(None)


class DebugState(t.Protocol):
    def ended(self) -> None:
        ...

    def evaluate(
        self,
        request: dap.EvaluateRequest,
    ) -> dap.EvaluateResponse:
        raise NotImplementedError()

    def continue_request(
        self,
        request: dap.ContinueRequest,
    ) -> dap.ContinueResponse:
        raise NotImplementedError()

    def get_scopes(
        self,
        request: dap.ScopesRequest,
    ) -> dap.ScopesResponse:
        raise NotImplementedError()

    def get_stacktrace(
        self,
        request: dap.StackTraceRequest,
    ) -> dap.StackTraceResponse:
        raise NotImplementedError()

    def get_threads(
        self,
        request: dap.ThreadsRequest,
    ) -> dap.ThreadsResponse:
        raise NotImplementedError()

    def get_variables(
        self,
        request: dap.VariablesRequest,
    ) -> dap.VariablesResponse:
        raise NotImplementedError()

    def set_variable(
        self,
        request: dap.SetVariableRequest,
    ) -> dap.SetVariableResponse:
        raise NotImplementedError()

    def step_in(
        self,
        request: dap.StepInRequest,
    ) -> None:
        raise NotImplementedError()

    def step_out(
        self,
        request: dap.StepOutRequest,
    ) -> None:
        raise NotImplementedError()

    def step_over(
        self,
        request: dap.NextRequest,
    ) -> None:
        raise NotImplementedError()


@dataclasses.dataclass
class AnsibleLineBreakpoint:

    id: int
    source: dap.Source
    source_breakpoint: dap.SourceBreakpoint
    breakpoint: dap.Breakpoint

    @property
    def path(self) -> str:
        return self.source.path or ""


class AnsibleDebugger(metaclass=Singleton):
    def __init__(self) -> None:
        self._connected = False
        self._cancel_token = SocketCancellationToken()
        self._recv_thread: t.Optional[threading.Thread] = None
        self._send_queue: queue.Queue[t.Optional[dap.ProtocolMessage]] = queue.Queue()
        self._da_connected = threading.Event()
        self._configuration_done = threading.Event()
        self._proto = DAProtocol(self)
        self._strategy_connected = threading.Condition()
        self._strategy: t.Optional[DebugState] = None

        self._thread_counter = 2  # 1 is always the main thread
        self._stackframe_counter = 1
        self._variable_counter = 1

        # Stores all the client breakpoints, key is the breakpoint number/id
        self._breakpoints: t.Dict[int, AnsibleLineBreakpoint] = {}
        self._breakpoint_counter = 1

        # Key is the path, the value is a list of the lines in that file where:
        #   None - Line is a continuation of a breakpoint range
        #   0    - Line is not something a breakpoint can be set at.
        #   1    - Line is the start of a breakpoint range
        #
        # The lines are 1 based with the 0 index representing 0 meaning a
        # breakpoint cannot be set until the first valid entry is found. A
        # continuation means the behaviour of the previous int in the list
        # continues to apply at that line.
        #
        # Examples of None would be
        #   - block/rescue/always - cannot stop on this, bp needs to be on a
        #     task.
        #   - import_* - These tasks are seen as the imported value not as
        #     itself
        #
        # Known Problems:
        #   - import_* tasks aren't present in the Playbook block. According to
        #     these rules it will be set as a breakpoint for the previous entry
        #   - roles in a play act like import_role - same as above
        #   - always and rescue aren't seen as an exact entry, will be set to
        #     the task that preceeds it
        #   - Won't contain the remaining lines of the file - bp checks will
        #     just have to use the last entry
        # FIXME: Somehow detect import entries to invalidate them.
        self._source_info: t.Dict[str, t.List[t.Optional[int]]] = {}

    @contextlib.contextmanager
    def with_strategy(
        self,
        strategy: DebugState,
    ) -> t.Generator[None, None, None]:
        with self._strategy_connected:
            if self._strategy:
                raise Exception("Strategy has already been registered")

            self._strategy = strategy
            self._strategy_connected.notify_all()

        try:
            if self._da_connected.is_set():
                self._configuration_done.wait()

            yield

        finally:
            with self._strategy_connected:
                self._strategy = None
                self._strategy_connected.notify_all()

    def next_thread_id(self) -> int:
        tid = self._thread_counter
        self._thread_counter += 1

        return tid

    def next_stackframe_id(self) -> int:
        sfid = self._stackframe_counter
        self._stackframe_counter += 1

        return sfid

    def next_variable_id(self) -> int:
        vid = self._variable_counter
        self._variable_counter += 1

        return vid

    def wait_for_config_done(
        self,
        timeout: t.Optional[float] = 10.0,
    ) -> None:
        """Waits until the debug config is done.

        Waits until the client has sent through the configuration done request
        that indicates no more initial configuration data is expected.

        Args:
            timeout: The maximum time, in seconds, to wait until the debug
                adapter is connected and ready.
        """
        self._da_connected.wait(timeout=timeout)
        # self._configuration_done.wait(timeout=timeout)
        # FIXME: Add check that this wasn't set on recv shutdown

    def get_breakpoint(
        self,
        path: str,
        line: int,
    ) -> t.Optional[AnsibleLineBreakpoint]:
        # FIXME: This could cause a deadlock
        if not self._connected:
            return None

        for b in self._breakpoints.values():
            if (
                b.path == path
                and (b.breakpoint.line is None or b.breakpoint.line <= line)
                and (b.breakpoint.end_line is None or b.breakpoint.end_line >= line)
            ):
                return b

        return None

    def start(
        self,
        addr: t.Tuple[str, int],
        mode: t.Literal["connect", "listen"],
    ) -> None:
        """Start the background server thread.

        Starts the background server thread which waits for an incoming request
        on the process' Unix Domain Socket and then subsequently starts the
        DAP server socket on the request that came in.

        Args:
            addr: The addr of the socket.
            mode: The socket mode to use, connect will connect to the addr
                while listen will bind to the addr and wait for a connection.
        """
        self._recv_thread = threading.Thread(
            target=self._recv_task,
            args=(addr, mode),
            name="ansibug-debugger",
        )
        self._recv_thread.start()

    def shutdown(self) -> None:
        """Shutdown the Debug Server.

        Marks the server as completed and signals the DAP server thread to
        shutdown.
        """
        log.debug("Shutting down DebugServer")
        self._cancel_token.cancel()
        if self._recv_thread:
            self._recv_thread.join()

        log.debug("DebugServer is shutdown")

    def send(
        self,
        msg: t.Optional[dap.ProtocolMessage],
    ) -> None:
        log.info("Sending to DA adapter %r", msg)
        self._send_queue.put(msg)

    def register_path_breakpoint(
        self,
        path: str,
        line: int,
        bp_type: int,
    ) -> None:
        """Register a valid breakpoint section.

        Registers a line as a valid breakpoint in a path. This registration is
        used when responding the breakpoint requests from the client.

        Args:
            path: The file path the line is set.
            line: The line the breakpoint is registered to.
            bp_type: Set to 1 for a valid breakpoint and 0 for an invalid
                breakpoint section.
        """
        # Ensure each new entry has a starting value of 0 which denotes that
        # a breakpoint cannot be set at the start of the file. It can only be
        # set when a line was registered.
        file_lines = self._source_info.setdefault(path, [0])
        file_lines.extend([None] * (1 + line - len(file_lines)))
        file_lines[line] = bp_type

        # FIXME: Put into common location to share with SetBreakpointRequest.
        for breakpoint in self._breakpoints.values():
            if breakpoint.path != path:
                continue

            source_breakpoint = breakpoint.source_breakpoint
            start_line = min(source_breakpoint.line, len(file_lines) - 1)
            end_line = start_line + 1

            line_type = file_lines[start_line]
            while line_type is None:
                start_line -= 1
                line_type = file_lines[start_line]

            while end_line < len(file_lines) and file_lines[end_line] is None:
                end_line += 1

            end_line = min(end_line - 1, len(file_lines))

            if line_type == 0:
                verified = False
                bp_msg = "Breakpoint cannot be set here."
            else:
                verified = True
                bp_msg = None

            if (
                breakpoint.breakpoint.verified != verified
                or breakpoint.breakpoint.line != start_line
                or breakpoint.breakpoint.end_line != end_line
            ):
                bp = breakpoint.breakpoint = dap.Breakpoint(
                    id=breakpoint.id,
                    verified=verified,
                    message=bp_msg,
                    source=breakpoint.source,
                    line=start_line,
                    end_line=end_line,
                )
                self.send(
                    dap.BreakpointEvent(
                        reason="changed",
                        breakpoint=bp,
                    )
                )

    def _get_strategy(self) -> DebugState:
        with self._strategy_connected:
            self._strategy_connected.wait_for(lambda: self._strategy is not None)
            return t.cast(DebugState, self._strategy)

    def _recv_task(
        self,
        addr: t.Tuple[str, int],
        mode: t.Literal["connect", "listen"],
    ) -> None:
        """Background server recv task.

        This is the task that continuously runs in the background waiting for
        DAP server to exchange debug messages. Depending on the mode requested
        the socket could be waiting for a connection or trying to connect to
        addr requested.

        In listen mode, the socket will attempt to wait for more DAP servers to
        connect to it in order to allow multiple connections as needed. This
        continues until the playbook is completed.

        Args:
            addr: The addr of the socket.
            mode: The socket mode to use, connect will connect to the addr
                while listen will bind to the addr and wait for a connection.
        """
        log.debug("Starting DAP server thread")

        try:
            while True:
                with wait_for_dap_server(addr, lambda: self._proto, mode, self._cancel_token) as mp_queue:
                    mp_queue.start()
                    self._da_connected.set()
                    self._connected = True
                    try:
                        while True:
                            resp = self._send_queue.get()
                            if not resp:
                                break

                            mp_queue.send(resp)

                    finally:
                        self._da_connected.clear()
                        self._connected = False

                if mode == "connect":
                    break

        except CancelledError:
            pass

        except Exception as e:
            log.exception(f"Unknown error in DAP thread: %s", e)

        # Ensures client isn't stuck waiting for something to never come.
        self._da_connected.set()
        self._configuration_done.set()
        with self._strategy_connected:
            if self._strategy:
                self._strategy.ended()

        log.debug("DAP server thread task ended")

    @functools.singledispatchmethod
    def process_message(
        self,
        msg: dap.ProtocolMessage,
    ) -> None:
        raise NotImplementedError(type(msg).__name__)

    @process_message.register
    def _(
        self,
        msg: dap.ConfigurationDoneRequest,
    ) -> None:
        resp = dap.ConfigurationDoneResponse(request_seq=msg.seq)
        self.send(resp)
        self._configuration_done.set()

    @process_message.register
    def _(
        self,
        msg: dap.ContinueRequest,
    ) -> None:
        strategy = self._get_strategy()
        resp = strategy.continue_request(msg)
        self.send(resp)

    @process_message.register
    def _(
        self,
        msg: dap.EvaluateRequest,
    ) -> None:
        strategy = self._get_strategy()
        resp = strategy.evaluate(msg)
        self.send(resp)

    @process_message.register
    def _(
        self,
        msg: dap.NextRequest,
    ) -> None:
        strategy = self._get_strategy()
        strategy.step_over(msg)
        self.send(dap.NextResponse(request_seq=msg.seq))

    @process_message.register
    def _(
        self,
        msg: dap.ScopesRequest,
    ) -> None:
        strategy = self._get_strategy()
        resp = strategy.get_scopes(msg)
        self.send(resp)

    @process_message.register
    def _(
        self,
        msg: dap.SetBreakpointsRequest,
    ) -> None:
        # FIXME: Deal with source_reference if set
        source_path = msg.source.path or ""
        source_info = self._source_info.get(source_path, None)

        # Clear out existing breakpoints for the source as each request should send the latest list for a source.
        self._breakpoints = {bpid: b for bpid, b in self._breakpoints.items() if b.path != source_path}

        breakpoint_info: t.List[dap.Breakpoint] = []
        for source_breakpoint in msg.breakpoints:
            bp_id = self._breakpoint_counter
            self._breakpoint_counter += 1

            bp: dap.Breakpoint
            if msg.source_modified:
                bp = dap.Breakpoint(
                    id=bp_id,
                    verified=False,
                    message="Cannot set breakpoint on a modified source.",
                    source=msg.source,
                )
                # I don't think we need to preserve this bp for later reference.
                breakpoint_info.append(bp)
                continue

            if not source_info:
                bp = dap.Breakpoint(
                    id=bp_id,
                    verified=False,
                    message="File has not been loaded by Ansible, cannot detect breakpoints yet.",
                    source=msg.source,
                    line=source_breakpoint.line,
                )

            else:
                start_line = min(source_breakpoint.line, len(source_info) - 1)
                end_line = start_line + 1

                line_type = source_info[start_line]
                while line_type is None:
                    start_line -= 1
                    line_type = source_info[start_line]

                while end_line < len(source_info) and source_info[end_line] is None:
                    end_line += 1

                end_line = min(end_line - 1, len(source_info))

                if line_type == 0:
                    verified = False
                    bp_msg = "Breakpoint cannot be set here."
                else:
                    verified = True
                    bp_msg = None

                bp = dap.Breakpoint(
                    id=bp_id,
                    verified=verified,
                    message=bp_msg,
                    source=msg.source,
                    line=start_line,
                    end_line=end_line,
                )

            self._breakpoints[bp_id] = AnsibleLineBreakpoint(
                id=bp_id,
                source=msg.source,
                source_breakpoint=source_breakpoint,
                breakpoint=bp,
            )
            breakpoint_info.append(bp)

        resp = dap.SetBreakpointsResponse(
            request_seq=msg.seq,
            breakpoints=breakpoint_info,
        )
        self.send(resp)

    @process_message.register
    def _(
        self,
        msg: dap.SetExceptionBreakpointsRequest,
    ) -> None:
        raise NotImplementedError()

    @process_message.register
    def _(
        self,
        msg: dap.SetVariableRequest,
    ) -> None:
        strategy = self._get_strategy()
        resp = strategy.set_variable(msg)
        self.send(resp)

    @process_message.register
    def _(
        self,
        msg: dap.StackTraceRequest,
    ) -> None:
        strategy = self._get_strategy()
        resp = strategy.get_stacktrace(msg)
        self.send(resp)

    @process_message.register
    def _(
        self,
        msg: dap.StepInRequest,
    ) -> None:
        strategy = self._get_strategy()
        strategy.step_in(msg)
        self.send(dap.StepInResponse(request_seq=msg.seq))

    @process_message.register
    def _(
        self,
        msg: dap.StepOutRequest,
    ) -> None:
        strategy = self._get_strategy()
        strategy.step_out(msg)
        self.send(dap.StepOutResponse(request_seq=msg.seq))

    @process_message.register
    def _(
        self,
        msg: dap.ThreadsRequest,
    ) -> None:
        strategy = self._get_strategy()
        resp = strategy.get_threads(msg)
        self.send(resp)

    @process_message.register
    def _(
        self,
        msg: dap.VariablesRequest,
    ) -> None:
        strategy = self._get_strategy()
        resp = strategy.get_variables(msg)
        self.send(resp)
