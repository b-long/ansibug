# -*- coding: utf-8 -*-
# Copyright (c) 2022 Jordan Borean
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import base64
import contextlib
import logging
import select
import socket
import threading
import types
import typing as t

log = logging.getLogger(__name__)


class SocketHelper:
    def __init__(
        self,
        use: str,
        family: socket.AddressFamily,
        kind: socket.SocketKind,
    ) -> None:
        self.use = use
        self._sock = socket.socket(family, kind)

    def __enter__(self) -> SocketHelper:
        log.debug("Entering %s socket", self.use)
        self._sock.__enter__()
        return self

    def __exit__(
        self,
        exception_type: t.Optional[t.Type[BaseException]] = None,
        exception_value: t.Optional[BaseException] = None,
        traceback: t.Optional[types.TracebackType] = None,
        **kwargs: t.Any,
    ) -> None:
        log.debug("Exiting %s socket", self.use)
        self._sock.__exit__(exception_type, exception_value, traceback, **kwargs)

    def close(self) -> None:
        self.__exit__()

    def bind(
        self,
        address: t.Any,
        listen: int = 1,
    ) -> None:
        log.debug("Socket %s binding to %s", self.use, address)
        self._sock.bind(address)
        self._sock.listen(listen)

    def connect(
        self,
        address: t.Any,
        cancel_token: SocketCancellationToken,
        timeout: float = 0,
    ) -> None:
        log.debug("Socket %s connecting to %s", self.use, address)
        cancel_token.connect(self._sock, address, timeout=timeout)
        log.debug("Socket %s connection successful", self.use)

    def accept(
        self,
        cancel_token: SocketCancellationToken,
        timeout: float = 0,
    ) -> t.Any:
        log.debug("Socket %s starting accept", self.use)
        conn, addr = cancel_token.accept(self._sock, timeout=timeout)
        log.debug("Socket %s accepted conn from %s", self.use, addr)

        # The underlying socket is no longer needed, only 1 connection is
        # expected per server socket.
        self._sock.close()
        self._sock = conn

        return addr

    def getsockname(self) -> t.Any:
        return self._sock.getsockname()

    def recv(
        self,
        n: int,
        cancel_token: SocketCancellationToken,
    ) -> bytes:
        """Wraps recv but ensures the data length specified is read."""
        buffer = bytearray(n)
        view = memoryview(buffer)
        read = 0

        while read < n:
            data_read = cancel_token.recv_into(self._sock, view[read:], n - read)
            read += data_read

            # On a socket shutdown 0 bytes will be read.
            if data_read == 0:
                break

        data = bytes(buffer[:read])
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Socket %s recv(%d): %s", self.use, n, base64.b64encode(data).decode())
        return data

    def send(
        self,
        data: bytes,
        cancel_token: SocketCancellationToken,
    ) -> None:
        """Wraps send but ensures all the data is sent."""
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Socket %s send: %s", self.use, base64.b64encode(data).decode())
        cancel_token.sendall(self._sock, data)

    def setsockopt(
        self,
        level: int,
        name: int,
        value: t.Union[int, bytes],
    ) -> None:
        self._sock.setsockopt(level, name, value)

    def shutdown(
        self,
        how: int,
    ) -> None:
        try:
            self._sock.shutdown(how)
        except OSError:
            pass


class SocketCancellationToken:
    def __init__(self) -> None:
        self._cancel_funcs: t.Dict[int, t.Callable[[], None]] = {}
        self._cancel_id = 0
        self._cancelled = False
        self._lock = threading.Lock()

    def accept(
        self,
        sock: socket.socket,
        timeout: float = 0,
    ) -> t.Tuple[socket.socket, t.Any]:
        with self.with_cancel(lambda: sock.shutdown(socket.SHUT_RDWR)):
            try:
                # When cancelled select will detect that sock is ready for a
                # read and accept() will raise OSError. In the rare event the
                # sockec was connected to and closed/shutdown between select
                # and the subsequent recv/send on the socket will act like it's
                # disconnected
                rd, _, _ = select.select([sock], [], [], timeout)
                if not rd:
                    raise TimeoutError("Timed out waiting for socket.accept()")

                return sock.accept()
            except OSError:
                if self._cancelled:
                    raise CancelledError()
                else:
                    raise

    def connect(
        self,
        sock: socket.socket,
        addr: t.Any,
        timeout: float = 0,
    ) -> None:
        with self.with_cancel(lambda: sock.shutdown(socket.SHUT_RDWR)):
            if timeout:
                sock.settimeout(timeout)

            try:
                sock.connect(addr)
            except OSError:
                if self._cancelled:
                    raise CancelledError()
                else:
                    raise

            else:
                # Set back into blocking mode.
                sock.settimeout(None)

    def recv_into(
        self,
        sock: socket.socket,
        buffer: t.Union[bytearray, memoryview],
        n: int,
    ) -> int:
        with self.with_cancel(lambda: sock.shutdown(socket.SHUT_RD)):
            res = sock.recv_into(buffer, n)
            if self._cancelled:
                raise CancelledError()

            return res

    def sendall(
        self,
        sock: socket.socket,
        data: bytes,
    ) -> None:
        with self.with_cancel(lambda: sock.shutdown(socket.SHUT_WR)):
            try:
                sock.sendall(data)
            except OSError:
                if self._cancel_funcs:
                    raise CancelledError()
                else:
                    raise

            if self._cancelled:
                raise CancelledError()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

            for cancel_id, func in self._cancel_funcs.items():
                log.debug("Canelling function with id %d", cancel_id)
                func()

            self._cancel_funcs = {}

    @contextlib.contextmanager
    def with_cancel(
        self,
        cancel_func: t.Callable[[], None],
    ) -> t.Generator[None, None, None]:
        with self._lock:
            if self._cancelled:
                raise CancelledError()

            cancel_id = self._cancel_id
            self._cancel_id += 1
            self._cancel_funcs[cancel_id] = cancel_func

        try:
            log.debug("Calling cancellable function with id %d", cancel_id)
            yield

        finally:
            with self._lock:
                self._cancel_funcs.pop(cancel_id, None)


class CancelledError(Exception):
    pass
