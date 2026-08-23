# FastAPI Service

## Bearer Authentication

The FastAPI dependency reads the Authorization header, validates the Bearer token, and rejects an invalid API key before request-body processing. Overenie tokenu prebieha pred volaním endpointu.

## Request Validation

Pydantic models validate JSON fields for each FastAPI endpoint. Validation errors return a structured response while authentication and authorization remain separate concerns.
