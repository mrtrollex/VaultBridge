"""Strict explicit write wire shapes; policy and mutation stay in the domain."""

from pydantic import ValidationError

from app.services.knowledge_spaces import WriteScope
from app.space_read_transport import InvalidReadRequest, selection


def parse_note_write(operation, data):
    from app.api.notes import AppendNoteRequest, CreateNoteRequest

    if type(data) is not dict:
        raise InvalidReadRequest()
    selected = selection(operation, data)
    model = CreateNoteRequest if operation == "create" else AppendNoteRequest
    fields = model.model_fields
    if data.keys() - fields.keys() - {"space"}:
        raise InvalidReadRequest()
    arguments = {key: value for key, value in data.items() if key != "space"}

    def unicode(value):
        if isinstance(value, str):
            value.encode("utf-8", errors="strict")
        elif isinstance(value, list):
            for item in value:
                unicode(item)

    try:
        unicode(list(arguments.values()))
        # Explicit optional null is invalid. Omission retains the old defaults.
        if any(value is None for value in arguments.values()):
            raise InvalidReadRequest()
        request = model.model_validate(arguments, strict=True).model_dump()
    except (ValidationError, UnicodeError):
        raise InvalidReadRequest() from None
    return WriteScope(selected), request


def execute_note_write(operations, operation, data, *, cancel=None):
    scope, request = parse_note_write(operation, data)
    method = operations.scoped_create_note if operation == "create" else operations.scoped_append_note
    result = method(scope, request, cancel=cancel)
    return {"space_id": result.space_id.value, "result": {
        "success": True, "path": result.result.path, "status": result.result.status,
    }}
