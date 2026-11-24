""" "Already Registered Organization?": a campus / branch request for an existing organization
(public: no sign-in; requirements Section 8).

1. POST /auth/campus-requests                          submit; a code is emailed
2. POST /auth/campus-requests/{id}/resend-otp          email a new code
3. POST /auth/campus-requests/{id}/verify              check the code; the request joins the
                                                       Main Admin's queue (GET /campus-requests)

These routes reach the application tables only through the narrow database functions of
migration 0021. No route here issues a token: the applicant signs in after approval.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from src.application.dtos.campus_request_application_dto import (
    CampusRequestApplicationRequest,
    CampusRequestCodeSentResponse,
    CampusRequestVerifiedResponse,
    VerifyCampusRequestRequest,
)
from src.application.use_cases.campus_request_application_use_cases import (
    CampusApplication,
    ResendCampusRequestCode,
    SubmitCampusRequest,
    VerifyCampusRequest,
)
from src.infrastructure.database.repositories.campus_request_application_repository import (
    SqlCampusRequestApplicationRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.api.v1.organization_registration_router import (
    _email,
    _limit_per_client_ip,
    _limits,
    _otp,
    _settings,
)
from src.presentation.dependencies import SessionDep

router = APIRouter(
    prefix="/auth/campus-requests",
    tags=["Campus requests (applicants)"],
    dependencies=[Depends(_limit_per_client_ip)],
)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CampusRequestCodeSentResponse)
async def submit_campus_request(
    body: CampusRequestApplicationRequest, request: Request, session: SessionDep
) -> CampusRequestCodeSentResponse:
    """Apply to add a campus to an existing organization. The organization must match its exact
    name and Organization ID. Stores a pending application and emails a 6-digit code."""
    settings = _settings(request)
    use_case = SubmitCampusRequest(
        SqlCampusRequestApplicationRepository(session),
        Argon2PasswordHasher(),
        _otp(request),
        _email(request),
        SqlTransaction(session),
        limits=_limits(settings),
        application_ttl_seconds=settings.org_registration_expire_minutes * 60,
    )
    sent = await use_case.execute(
        CampusApplication(
            organization_name=body.organization_name,
            organization_id=body.organization_id,
            campus_name=body.campus_name,
            full_name=body.admin_full_name,
            username=body.username,
            email=body.email,
            phone=body.phone_number,
            password=body.password,
        )
    )
    return CampusRequestCodeSentResponse.from_result(sent)


@router.post("/{application_id}/resend-otp", response_model=CampusRequestCodeSentResponse)
async def resend_campus_request_code(
    application_id: UUID, request: Request, session: SessionDep
) -> CampusRequestCodeSentResponse:
    """Email a new code; the previous one stops working."""
    use_case = ResendCampusRequestCode(
        SqlCampusRequestApplicationRepository(session),
        _otp(request),
        _email(request),
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return CampusRequestCodeSentResponse.from_result(await use_case.execute(application_id))


@router.post("/{application_id}/verify", response_model=CampusRequestVerifiedResponse)
async def verify_campus_request(
    application_id: UUID, body: VerifyCampusRequestRequest, request: Request, session: SessionDep
) -> CampusRequestVerifiedResponse:
    """Check the code. A right code sends the request to the Main Admin for approval; the
    applicant cannot sign in until it is approved."""
    use_case = VerifyCampusRequest(
        SqlCampusRequestApplicationRepository(session), _otp(request), SqlTransaction(session)
    )
    return CampusRequestVerifiedResponse.from_entity(await use_case.execute(application_id, body.otp))
