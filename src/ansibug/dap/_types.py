# -*- coding: utf-8 -*-
# Copyright (c) 2022 Jordan Borean
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import dataclasses
import typing as t


@dataclasses.dataclass()
class Breakpoint:
    """Breakpoint information.

    Information about a breakpoint created in SetBreakpointsRequest,
    SetFunctionBreakpointsRequest, SetInstructionBreakspointsRequest, or
    SetDataBreakpointsRequest.

    Args:
        id: Optional identifier for the breakpoint.
        verified: The breakpoint could be set.
        message: Option explanation on the state of the breakpoint.
        source: Source where the breakpoint is located.
        line: The start line of the actual range covered by the breakpoint.
        column: The start column of the actual range covered by the breakpoint.
        end_line: End line of the actual range covered by the breakpoint.
        end_column: End column of the actual range covered by the breakpoint.
            If no end_line is specified, then the column is assumed to be in
            the start_line.
        instruction_reference: Optional memory reference to where the
            breakpoint is set.
        offset: Optional offset from the instruction reference.
    """

    id: t.Optional[int] = None
    verified: bool = False
    message: t.Optional[str] = None
    source: t.Optional[Source] = None
    line: t.Optional[int] = None
    column: t.Optional[int] = None
    end_line: t.Optional[int] = None
    end_column: t.Optional[int] = None
    instruction_reference: t.Optional[str] = None
    offset: t.Optional[int] = None

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "id": self.id,
            "verified": self.verified,
            "message": self.message,
            "source": self.source.pack() if self.source else None,
            "line": self.line,
            "column": self.column,
            "endLine": self.end_line,
            "endColumn": self.end_column,
            "instructionReference": self.instruction_reference,
            "offset": self.offset,
        }

    @classmethod
    def unpack(
        cls,
        obj: t.Dict[str, t.Any],
    ) -> Breakpoint:
        return Breakpoint(
            id=obj.get("id", None),
            verified=obj.get("verified", False),
            message=obj.get("message", None),
            source=Source.unpack(obj["source"]) if "source" in obj else None,
            line=obj.get("line", None),
            column=obj.get("column", None),
            end_line=obj.get("endLine", None),
            end_column=obj.get("endColumn", None),
            instruction_reference=obj.get("instructionReference", None),
            offset=obj.get("offset", None),
        )


@dataclasses.dataclass()
class Capabilities:
    """Capabilities of a debug adapter.

    Information about the capabilities of a debug adapter.

    Args:
        supports_configuration_done_request: The debug adapter supports the
            ConfigurationDoneRequest.
    """

    supports_configuration_done_request: bool = False

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "supportsConfigurationDoneRequest": self.supports_configuration_done_request,
        }

    @classmethod
    def unpack(
        cls,
        obj: t.Dict[str, t.Any],
    ) -> Capabilities:
        return Capabilities(
            supports_configuration_done_request=obj.get("supportsConfigurationDoneRequest", False),
        )


@dataclasses.dataclass()
class ExceptionFilterOptions:
    """Exception filter options.

    Used to specify an exception filter together with a condition for the
    SetExceptionBreakpoints request.

    Args:
        filter_id: The ID of the exception filter.
        condition: Optional condition for the filter.
    """

    filter_id: str
    condition: t.Optional[str] = None

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "filterId": self.filter_id,
            "condition": self.condition,
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> ExceptionFilterOptions:
        return ExceptionFilterOptions(
            filter_id=body["filterId"],
            condition=body.get("condition", None),
        )


@dataclasses.dataclass()
class ExceptionOptions:
    """Configuration options to a set of exceptions.

    Assigns configuration options to a set of exceptions.

    Args:
        path: A path that selects a single or multiple exceptions in a tree.
            By convention the first segment of the path is a category that is
            used to group exceptions in the UI.
        break_mode: Condition when a thrown exception should result in a break.
    """

    path: t.List[ExceptionPathSegment]
    break_mode: t.Literal["never", "always", "unhandled", "userUnhandled"]

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "path": [p.pack() for p in self.path],
            "breakMode": self.break_mode,
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> ExceptionOptions:
        path: t.List[ExceptionPathSegment] = []
        for p in body.get("path", []):
            path.append(ExceptionPathSegment.unpack(p))

        return ExceptionOptions(
            path=path,
            break_mode=body["breakMode"],
        )


@dataclasses.dataclass()
class ExceptionPathSegment:
    """Represents a segment in a path.

    Represents a segment in a path that is used to match leafs or nodes in a
    tree of exceptions.

    Args:
        negate: Controls the matching behaviour of the names.
        names: The values to match (or not match if negate=True).
    """

    negate: bool = False
    names: t.List[str] = dataclasses.field(default_factory=list)

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "negate": self.negate,
            "names": self.names,
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> ExceptionPathSegment:
        return ExceptionPathSegment(
            negate=body.get("negate", False),
            names=body.get("names", []),
        )


@dataclasses.dataclass()
class Message:
    """A structured message object.

    A structured message object used to return errors from requests.
    """

    id: int
    format: str
    variables: t.Dict[str, str] = dataclasses.field(default_factory=dict)
    send_telemetry: bool = False
    show_user: bool = False
    url: t.Optional[str] = None
    url_label: t.Optional[str] = None

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "id": self.id,
            "format": self.format,
            "variables": self.variables,
            "sendTelemetry": self.send_telemetry,
            "showUser": self.show_user,
            "url": self.url,
            "urlLabel": self.url_label,
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> Message:
        return Message(
            id=body["id"],
            format=body["format"],
            variables=body.get("variables", {}),
            send_telemetry=body.get("sendTelemetry", False),
            show_user=body.get("showUser", False),
            url=body.get("url", None),
            url_label=body.get("urlLabel", None),
        )


@dataclasses.dataclass()
class Source:
    """Descriptor for source code.

    A source is used to describe source code. It is returned from the debug
    adapter as part of a StackFrame and it is used by clients when specifying
    breakpoints.

    Args:
        name: The short name of the source.
        path: The path of the source to be shown in the UI. It is used to
            locate the source if source_reference is greater than 0.
        source_reference: If greater than 0, the contents of the source must be
            retrieved through the SourceRequest. The id is only valid for a
            session.
        presentation_hint: How to present the source in the UI. A value of
            deemphasize can be used to indicate the source is not available or
            that it is skipped on stepping.
        origin: Origin of this source.
        sources: List of sources that are related to this source.
        adapter_data: Optional opaque data to associate with the source. The
            client does not interpret this data.
        checksums: Checksum associated with this file.
    """

    name: t.Optional[str] = None
    path: t.Optional[str] = None
    source_reference: int = 0
    presentation_hint: t.Literal["normal", "emphasize", "deemphasize"] = "normal"
    origin: t.Optional[str] = None
    sources: t.List[Source] = dataclasses.field(default_factory=list)
    adapter_data: t.Any = None
    checksums: t.List[Checksum] = dataclasses.field(default_factory=list)

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "name": self.name,
            "path": self.path,
            "sourceReference": self.source_reference,
            "presentationHint": self.presentation_hint,
            "origin": self.origin,
            "sources": [s.pack() for s in self.sources],
            "adapterData": self.adapter_data,
            "checksums": [c.pack() for c in self.checksums],
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> Source:
        return Source(
            name=body.get("name", None),
            path=body.get("path", None),
            source_reference=body.get("sourceReference", None) or 0,
            presentation_hint=body.get("presentationHint", "normal"),
            origin=body.get("origin", None),
            sources=[Source.unpack(s) for s in body.get("sources", [])],
            adapter_data=body.get("adapterData", None),
            checksums=[Checksum.unpack(c) for c in body.get("checksums", [])],
        )


@dataclasses.dataclass()
class SourceBreakpoint:
    """Properties of a breakpoint.

    The properties of a breakpoint or logpoint passed to the
    SetBreakpoinsRequest.

    Args:
        line: The source line of the breakpoint or logpoint.
        column: The optional source column of the breakpoint.
        condition: An optional expression for conditional breakpoints.
        hit_condition: An optional expression that controls how many hits of
            the brekapoint are ignored.
        log_message: Do not break but log the message on a break.
    """

    line: int
    column: t.Optional[int] = None
    condition: t.Optional[str] = None
    hit_condition: t.Optional[str] = None
    log_message: t.Optional[str] = None

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "line": self.line,
            "column": self.column,
            "condition": self.condition,
            "hitCondition": self.hit_condition,
            "logMessage": self.log_message,
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> SourceBreakpoint:
        return SourceBreakpoint(
            line=body["line"],
            column=body.get("column", None),
            condition=body.get("condition", None),
            hit_condition=body.get("hitCondition", None),
            log_message=body.get("logMessage", None),
        )


@dataclasses.dataclass()
class Checksum:
    """The checksum of an item.

    Describes the checksum of an item. The known algorithms are "MD5", "SHA1",
    "SHA256", and "timestamp".

    Args:
        algorithm: The algorithm used to calculate this checksum.
        checksum: The value of the checksum, encoded as a hexadecimal value.
    """

    algorithm: t.Literal["MD5", "SHA1", "SHA256", "timestamp"]
    checksum: str

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "algorithm": self.algorithm,
            "checksum": self.checksum,
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> Checksum:
        return Checksum(
            algorithm=body["algorithm"],
            checksum=body["checksum"],
        )


@dataclasses.dataclass()
class Thread:
    """A thread.

    Represents a thread on the debuggee.

    Args:
        id: The unique identifier for the thread.
        name: The name of the thread.
    """

    id: int
    name: str

    def pack(self) -> t.Dict[str, t.Any]:
        return {
            "id": self.id,
            "name": self.name,
        }

    @classmethod
    def unpack(
        cls,
        body: t.Dict[str, t.Any],
    ) -> Thread:
        return Thread(
            id=body["id"],
            name=body["name"],
        )