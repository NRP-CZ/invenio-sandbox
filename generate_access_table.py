"""Set up test users, communities and memberships.

Run with:

    invenio shell < setup_tests.py

(needs an active app context, which `invenio shell` provides).
"""
import shutil
from pathlib import Path
from invenio_rdm_records.services import RDMRecordService
from dataclasses import dataclass, field, fields
from typing import Optional, cast
import csv
import itertools
import os
import io
from tqdm import tqdm
import contextlib
from flask import g, current_app, has_request_context
from oarepo_runtime import current_runtime
import sys

from datetime import datetime, timezone

import click
from flask_security.utils import hash_password, logout_user
from invenio_access.permissions import system_identity
from invenio_accounts.proxies import current_datastore
from invenio_communities.communities.records.api import Community
from invenio_communities.members.errors import AlreadyMemberError
from invenio_communities.proxies import current_communities
from invenio_db import db
from invenio_pidstore.errors import PIDDoesNotExistError
from invenio_records_resources.proxies import current_service_registry
from invenio_records_resources.services.errors import PermissionDeniedError
from invenio_requests.proxies import current_requests_service
from invenio_users_resources.proxies import current_users_service

USERS = {}
for _role in current_app.config["COMMUNITIES_ROLES"]:
    _name = _role["name"]
    USERS[f"{_name}@demo.org"] = _name
    USERS[f"{_name}_indiv@demo.org"] = _name

ZENSICAL_TOML_TEMPLATE = '''[project]
site_url = "https://www.example.com/"
site_name = "Documentation"

nav = [
  {{NAV_ENTRIES}}
]

extra_css = ["stylesheets/extra.css"]

[project.theme]
language = "en"
features = [
  "announce.dismiss",
  "content.code.annotate",
  "content.code.copy",
  "content.code.select",
  "content.footnote.tooltips",
  "content.tabs.link",
  "content.tooltips",
  "navigation.footer",
  "navigation.indexes",
  "navigation.instant",
  "navigation.instant.prefetch",
  "navigation.path",
  "navigation.sections",
  "navigation.top",
  "navigation.tracking",
  "search.highlight",
]

[[project.theme.palette]]
media = "(prefers-color-scheme)"
toggle.icon = "lucide/sun-moon"
toggle.name = "Switch to light mode"

[[project.theme.palette]]
media = "(prefers-color-scheme: light)"
scheme = "default"
toggle.icon = "lucide/sun"
toggle.name = "Switch to dark mode"

[[project.theme.palette]]
media = "(prefers-color-scheme: dark)"
scheme = "slate"
toggle.icon = "lucide/moon"
toggle.name = "Switch to system preference"

[project.markdown_extensions]
abbr = {}
admonition = {}
attr_list = {}
def_list = {}
footnotes = {}
md_in_html = {}
toc.permalink = true
pymdownx.arithmatex.generic = true
pymdownx.betterem = {}
pymdownx.caret = {}
pymdownx.details = {}
pymdownx.emoji.emoji_generator = "zensical.extensions.emoji.to_svg"
pymdownx.emoji.emoji_index = "zensical.extensions.emoji.twemoji"
pymdownx.highlight.anchor_linenums = true
pymdownx.highlight.line_spans = "__span"
pymdownx.highlight.pygments_lang_class = true
pymdownx.inlinehilite = {}
pymdownx.keys = {}
pymdownx.magiclink = {}
pymdownx.mark = {}
pymdownx.smartsymbols = {}
pymdownx.superfences.custom_fences = [
  { name = "mermaid", class = "mermaid", format = "pymdownx.superfences.fence_code_format" },
]
pymdownx.tabbed.alternate_style = true
pymdownx.tabbed.combine_header_slug = true
pymdownx.tasklist.custom_checkbox = true
pymdownx.tilde = {}
'''

EXTRA_CSS = '''/* Compact table styling */
.md-typeset__table {
  line-height: 1;
}

.md-typeset__table table {
  font-size: 0.8rem;
  border-collapse: collapse;
}

.md-typeset__table th,
.md-typeset__table td {
  padding: 0.4em 0.6em;
}

/* Wrap column headers on underscore */
.md-typeset__table th {
  white-space: pre-wrap;
  word-break: break-all;
}

/* Allow full width content */
.md-grid {
  max-width: none !important;
}
'''

INDEX_MD_TEMPLATE = '''# Permission check results

## Individual

{individual_links}

## Communities

{communities_links}
'''

AUTHENTICATED_USER = "standalone@demo.org"
INDIVIDUAL_SUBMITTER = "individual_submitter@demo.org"
ANONYMOUS_USER = "anonymous@demo.org"

GLOBAL_ROLE = "submitter"
GLOBAL_ROLE_USERS = [email for email in USERS if email.endswith("_indiv@demo.org")] + [INDIVIDUAL_SUBMITTER]

COMMUNITIES = {}
for _workflow in current_app.config["WORKFLOWS"]:
    if _workflow.code == current_app.config["WORKFLOWS_DEFAULT_WORKFLOW"]:
        continue
    COMMUNITIES[f"public-{_workflow.code}"] = (_workflow.code, True)
    COMMUNITIES[f"private-{_workflow.code}"] = (_workflow.code, False)

def format_readers_table(results: list[MakeRecordResult]) -> list[list[str]]:
    """Formatter for a bucket of MakeRecordResult sharing one output path.

    Rows are the union of readers seen across all results' draft_read/
    published_read (usually just one result, since "readers" is passed
    through as a whole collection to a single make_record() call rather
    than being cartesian-expanded); columns are "draft" and "published".
    Returns a list of rows (including the header), ready for csv.writer.
    """
    readers = sorted({
        reader
        for result in results
        for reader in (*result.draft_read.keys(), *result.published_read.keys())
    })
    rows = [["reader", "draft", "published"]]
    for reader in readers:
        for result in results:
            if reader not in result.draft_read and reader not in result.published_read:
                continue
            rows.append([
                reader,
                {True: "ok", False: "failed", None: ""}[result.draft_read.get(reader)],
                {True: "ok", False: "failed", None: ""}[result.published_read.get(reader)],
            ])
    return rows


def _format_bool_fields_table(results, row_label_field):
    rows = [[row_label_field, *RESULT_BOOL_FIELDS]]
    for result in results:
        row = [getattr(result, row_label_field)]
        for field_name in RESULT_BOOL_FIELDS:
            value = getattr(result, field_name)
            row.append({True: "ok", False: "failed", None: ""}[value])
        rows.append(row)
    return rows


def format_bool_fields_by_creator(results: list[MakeRecordResult]) -> list[list[str]]:
    """Formatter for a bucket of results where "submitter" varies and every
    other argument (e.g. a fixed reviewer) is constant: one row per creator,
    one column per MakeRecordResult bool field."""
    return _format_bool_fields_table(results, "creator")


def format_bool_fields_by_reviewer(results: list[MakeRecordResult]) -> list[list[str]]:
    """Formatter for a bucket of results where "reviewer" varies and every
    other argument (e.g. a fixed submitter/creator) is constant: one row per
    reviewer, one column per MakeRecordResult bool field."""
    return _format_bool_fields_table(results, "reviewer")


ALL_COMMUNITY_USERS = set(USERS)
COMMUNITY_SLUGS = set(COMMUNITIES)
ACCESS_LEVELS = {"public", "restricted"}
ALL_READERS = {*USERS, AUTHENTICATED_USER, INDIVIDUAL_SUBMITTER, ANONYMOUS_USER}

def distinct_reviewers(**kwargs):
    return kwargs.get("submitter") != kwargs.get("reviewer")

CHECKS = [
    {
        "submitter": INDIVIDUAL_SUBMITTER,
        "reviewer": INDIVIDUAL_SUBMITTER,
        "publish_directly": False,
        "community_slug": None,
        "publish_with_request": True,
        "access": {"public", "restricted"},
        "readers": ALL_READERS,
        "path": "individual/read/{access}.csv",
        "formatter": format_readers_table
    },

    # A. individual, direct publish, self only (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "publish_directly": True,
        "access": ACCESS_LEVELS,
        "path": "individual/direct_publish/{access}_self.csv",
        "formatter": format_bool_fields_by_creator,
    },

    # B. individual, publish-with-request, self-approve (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "publish_with_request": True,
        "access": ACCESS_LEVELS,
        "path": "individual/publish_draft_request_self/{access}_self.csv",
        "formatter": format_bool_fields_by_creator,
    },

    # C. individual, publish-with-request, fixed reviewer (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "reviewer": ALL_COMMUNITY_USERS,
        "publish_with_request": True,
        "access": ACCESS_LEVELS,
        "path": "individual/publish_draft_request_by_reviewer/{access}_{reviewer}.csv",
        "formatter": format_bool_fields_by_creator,
        "filter": distinct_reviewers,
    },

    # D. individual, publish-with-request, fixed creator (rows = reviewers)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "reviewer": ALL_COMMUNITY_USERS,
        "publish_with_request": True,
        "access": ACCESS_LEVELS,
        "path": "individual/publish_draft_request_by_creator/{access}_{submitter}.csv",
        "formatter": format_bool_fields_by_reviewer,
        "filter": distinct_reviewers,
    },

    # E. community, direct publish, self only (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "community_slug": COMMUNITY_SLUGS,
        "publish_directly": True,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/direct_publish/{access}_self.csv",
        "formatter": format_bool_fields_by_creator,
    },

    # F. community, publish-with-request, self-approve (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "community_slug": COMMUNITY_SLUGS,
        "publish_with_request": True,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/direct_publish_after_draft_request/{access}_self.csv",
        "formatter": format_bool_fields_by_creator,
    },

    # G. community, publish-with-request, fixed reviewer (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "reviewer": ALL_COMMUNITY_USERS,
        "community_slug": COMMUNITY_SLUGS,
        "publish_with_request": True,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/publish_draft_request_by_reviewer/{access}_{reviewer}.csv",
        "formatter": format_bool_fields_by_creator,
        "filter": distinct_reviewers,
    },

    # H. community, publish-with-request, fixed creator (rows = reviewers)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "reviewer": ALL_COMMUNITY_USERS,
        "community_slug": COMMUNITY_SLUGS,
        "publish_with_request": True,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/publish_draft_request_by_creator/{access}_{submitter}.csv",
        "formatter": format_bool_fields_by_reviewer,
        "filter": distinct_reviewers,
    },

    # I. community review flow, self-approve (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "community_slug": COMMUNITY_SLUGS,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/self_review/{access}_self.csv",
        "formatter": format_bool_fields_by_creator,
    },

    # J. community review flow, fixed reviewer (rows = creators)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "reviewer": ALL_COMMUNITY_USERS,
        "community_slug": COMMUNITY_SLUGS,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/review_by_reviewer/{access}_{reviewer}.csv",
        "formatter": format_bool_fields_by_creator,
        "filter": distinct_reviewers,
    },

    # K. community review flow, fixed creator (rows = reviewers)
    {
        "submitter": ALL_COMMUNITY_USERS,
        "reviewer": ALL_COMMUNITY_USERS,
        "community_slug": COMMUNITY_SLUGS,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/review_by_creator/{access}_{submitter}.csv",
        "formatter": format_bool_fields_by_reviewer,
        "filter": distinct_reviewers,
    },

    # L. community read flow, fixed creator and reviewer
    {
        "submitter": "submitter@demo.org",
        "reviewer": "curator@demo.org",
        "community_slug": COMMUNITY_SLUGS,
        "access": ACCESS_LEVELS,
        "path": "communities/{community_slug}/read/{access}.csv",
        "readers": ALL_READERS,
        "formatter": format_readers_table,
    },
]


def setup_users():
    for email in USERS | {AUTHENTICATED_USER: None, INDIVIDUAL_SUBMITTER: None}:
        user = current_datastore.get_user(email)
        if user is None:
            user = current_datastore.create_user(
                email=email,
                password=hash_password("123456"),
                active=True,
                confirmed_at=datetime.now(timezone.utc),
            )
            click.secho(f"user {email}: created", fg="green")
        else:
            click.secho(f"user {email}: already existed", fg="yellow")
        user.preferences = {
            **user.preferences,
            "visibility": "public",
            "email_visibility": "public",
        }
    db.session.commit()

    # Reindex users synchronously (no celery worker consuming the bulk-index
    # queue in this sandbox), so entity resolution (e.g. owned_by expand)
    # finds them instead of falling back to the ghost/ "Deleted user" schema.
    for email in USERS | {AUTHENTICATED_USER: None, INDIVIDUAL_SUBMITTER: None}:
        user = current_datastore.get_user(email)
        current_users_service.indexer.index_by_id(user.id)


def setup_global_roles():
    role = current_datastore.find_role(GLOBAL_ROLE)
    if role is None:
        role = current_datastore.create_role(id=GLOBAL_ROLE, name=GLOBAL_ROLE)
        click.secho(f"role {GLOBAL_ROLE}: created", fg="green")
    else:
        click.secho(f"role {GLOBAL_ROLE}: already existed", fg="yellow")

    for email in GLOBAL_ROLE_USERS:
        user = current_datastore.get_user(email)
        if current_datastore.add_role_to_user(user, role):
            click.secho(f"user {email}: added to role {GLOBAL_ROLE}", fg="green")
        else:
            click.secho(f"user {email}: already in role {GLOBAL_ROLE}", fg="yellow")

    db.session.commit()


def setup_communities():
    for slug, (workflow_code, public) in COMMUNITIES.items():
        try:
            Community.pid.resolve(slug)
            click.secho(f"community {slug}: already existed", fg="yellow")
        except PIDDoesNotExistError:
            current_communities.service.create(
                system_identity,
                {
                    "slug": slug,
                    "metadata": {"title": workflow_code},
                    "access": {"visibility": "public" if public else "restricted"},
                    "custom_fields": {
                        "workflow": workflow_code,
                        "allowed_workflows": [workflow_code],
                    },
                },
            )
            click.secho(f"community {slug}: created", fg="green")


def setup_members():
    for slug in COMMUNITIES:
        community_id = str(Community.pid.resolve(slug).id)
        for email, role in USERS.items():
            user = current_datastore.get_user(email)
            try:
                current_communities.service.members.add(
                    system_identity,
                    community_id,
                    {
                        "members": [{"type": "user", "id": str(user.id)}],
                        "role": role,
                    },
                )
                click.secho(f"member {email} ({role}) of {slug}: created", fg="green")
            except AlreadyMemberError:
                click.secho(f"member {email} ({role}) of {slug}: already existed", fg="yellow")

rdm_service = cast(RDMRecordService, current_runtime.models['datasets'].service)
draft_file_service = current_runtime.models['datasets'].draft_file_service
requests_service = current_service_registry.get("requests")


@dataclass
class MakeRecordResult:
    draft: Optional[str] = None
    published: Optional[str] = None
    creator: Optional[str] = None
    reviewer: Optional[str] = None
    draft_created: Optional[bool] = None
    file_uploaded: Optional[bool] = None
    review_created: Optional[bool] = None
    review_submitted: Optional[bool] = None
    review_accepted: Optional[bool] = None
    published_directly: Optional[bool] = None
    publish_request_created: Optional[bool] = None
    publish_request_submitted: Optional[bool] = None
    publish_request_accepted: Optional[bool] = None
    draft_read: dict = field(default_factory=dict)
    published_read: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)


@contextlib.contextmanager
def reader_identity(reader_email):
    """Establish the identity to check reads with: a logged-in user, or - for the
    ANONYMOUS_USER sentinel, which is never created as a real account - an
    anonymous identity (matching how current_runtime.login_user() itself
    establishes a request context and identity)."""
    if reader_email != ANONYMOUS_USER:
        with current_runtime.login_user(reader_email):
            yield
        return

    ctx = None if has_request_context() else current_app.test_request_context()
    if ctx is not None:
        ctx.push()
    try:
        logout_user()
        yield
    finally:
        if ctx is not None:
            ctx.pop()


def make_record(submitter, title, publish_directly: bool=False, community_slug: str=None, reviewer: str=None, publish_with_request: bool=False, access: str="public",
    readers: list[str] | None = None):
    result = MakeRecordResult(creator=submitter, reviewer=reviewer)

    def log(step, call, exc_or_message=None):
        if exc_or_message is None:
            outcome = "ok"
        elif isinstance(exc_or_message, BaseException):
            outcome = f"{type(exc_or_message).__name__}: {exc_or_message}"
        else:
            outcome = exc_or_message
        result.logs.append(f"{step}: {call} -> {outcome}")

    with current_runtime.login_user(submitter):
        create_data = {
            "access": {"record": access, "files": access},
            "metadata": {
                "title": title,
                "publication_date": "2026-01-01",
                "resource_type": {"id": "c_ddb1"},
                "creators": [
                    {
                        "person_or_org": {
                            "type": "personal",
                            "given_name": "Jane",
                            "family_name": "Doe",
                            "name": "Doe, Jane",
                        }
                    }
                ],
            }
        }
        if community_slug and (publish_directly or publish_with_request):
            # attach the draft to the community up front (instead of via a
            # review request), so its applicable workflow/permissions are
            # the community's, and we can check whether that workflow
            # allows a direct/request-based (non-reviewed) publish for this
            # user's role.
            community_id = str(Community.pid.resolve(community_slug).id)
            create_data["parent"] = {"communities": {"default": community_id}}

        call = f"rdm_service.create(identity={submitter!r}, data={create_data!r})"
        try:
            draft = rdm_service.create(g.identity, create_data)
        except Exception as e:
            log("draft_created", call, e)
            result.draft_created = False
            return result

        errors = draft.to_dict().get("errors")
        if errors:
            log("draft_created", call, f"validation errors: {errors}")
            result.draft_created = False
            return result
        log("draft_created", call)
        result.draft = draft.id
        result.draft_created = True

    # Reader draft-read checks run as sibling `with` blocks, not nested
    # inside the creator's login_user() block above: current_runtime's
    # login_user() context manager fully logs out on exit rather than
    # restoring a previous identity when reused inside an already-active
    # request context, so nesting it here would silently leave the
    # ambient identity anonymous for the rest of the function (breaking
    # e.g. the file upload right below, which needs to act as the creator).
    if readers:
        for reader_email in readers:
            with reader_identity(reader_email):
                call = f"rdm_service.read_draft(identity={reader_email!r}, id_={draft.id!r})"
                try:
                    rdm_service.read_draft(g.identity, draft.id)
                except Exception as e:
                    log(f"draft_read[{reader_email}]", call, e)
                    result.draft_read[reader_email] = False
                else:
                    log(f"draft_read[{reader_email}]", call)
                    result.draft_read[reader_email] = True

    with current_runtime.login_user(submitter):
        content = b"hi!"
        call = (
            f"draft_file_service.init_files(identity={submitter!r}, id_={draft.id!r}, data=[{{'key': 'test.txt'}}]); "
            f"draft_file_service.set_file_content(identity={submitter!r}, id_={draft.id!r}, key='test.txt', content_length={len(content)}); "
            f"draft_file_service.commit_file(identity={submitter!r}, id_={draft.id!r}, key='test.txt')"
        )
        try:
            draft_file_service.init_files(g.identity, draft.id, data=[{"key": "test.txt"}])
            draft_file_service.set_file_content(
                g.identity, draft.id, "test.txt", io.BytesIO(content), content_length=len(content)
            )
            draft_file_service.commit_file(g.identity, draft.id, "test.txt")
        except Exception as e:
            log("file_uploaded", call, e)
            result.file_uploaded = False
        else:
            log("file_uploaded", call)
            result.file_uploaded = True

        if community_slug and publish_directly:
            call = f"rdm_service.publish(identity={submitter!r}, id_={draft.id!r})"
            try:
                published = rdm_service.publish(g.identity, draft.id)
            except Exception as e:
                log("published_directly", call, e)
                result.published_directly = False
                return result
            if not published.to_dict().get("is_published"):
                log("published_directly", call, f"unexpected result: {published.to_dict()}")
                result.published_directly = False
                return result
            log("published_directly", call)
            result.published_directly = True
            result.published = published.id

        elif publish_with_request:
            # re-read the draft: draft._record is stale after the file
            # upload above (a separate service call, on its own fetched
            # record), and publish_draft's can_create validates the topic's
            # files as passed in, so a stale record fails with "missing
            # uploaded files" even though the file was actually committed.
            # (Works whether or not community_slug is set - the draft was
            # already attached to the community at creation time above.)
            fresh_draft = rdm_service.read_draft(g.identity, draft.id)
            call = f"requests_service.create(identity={submitter!r}, data={{}}, request_type='publish_draft', topic=<record {draft.id}>)"
            try:
                request_item = requests_service.create(
                    g.identity, {}, "publish_draft", topic=fresh_draft._record,
                )
            except Exception as e:
                # note: "publish_draft" is only registered on
                # IndividualWorkflow (the "open" workflow), not on any
                # CommunityWorkflow - community-attached drafts can never
                # use this request type (raises RequestTypeNotInWorkflowError).
                log("publish_request_created", call, e)
                result.publish_request_created = False
                return result
            log("publish_request_created", call)
            result.publish_request_created = True

            call = f"requests_service.execute_action(identity={submitter!r}, id_={request_item.id!r}, action='submit')"
            try:
                submitted = requests_service.execute_action(g.identity, request_item.id, "submit")
            except Exception as e:
                log("publish_request_submitted", call, e)
                result.publish_request_submitted = False
                return result
            if submitted.to_dict().get("status") != "submitted":
                log("publish_request_submitted", call, f"unexpected result: {submitted.to_dict()}")
                result.publish_request_submitted = False
                return result
            log("publish_request_submitted", call)
            result.publish_request_submitted = True

            approver = reviewer or submitter
            with current_runtime.login_user(approver):
                call = f"requests_service.execute_action(identity={approver!r}, id_={submitted.id!r}, action='accept')"
                try:
                    accepted = requests_service.execute_action(g.identity, submitted.id, "accept")
                except Exception as e:
                    log("publish_request_accepted", call, e)
                    result.publish_request_accepted = False
                    return result
                if accepted.to_dict().get("status") != "accepted":
                    log("publish_request_accepted", call, f"unexpected result: {accepted.to_dict()}")
                    result.publish_request_accepted = False
                    return result
                log("publish_request_accepted", call)
                result.publish_request_accepted = True
                call = f"rdm_service.read(identity='system', id_={draft.id!r})"
                try:
                    result.published = rdm_service.read(system_identity, draft.id).id
                except Exception as e:
                    log("published (post publish_request_accepted read)", call, e)
                else:
                    log("published (post publish_request_accepted read)", call)

        elif community_slug:
            community_id = str(Community.pid.resolve(community_slug).id)
            call = (
                f"rdm_service.review.create(identity={submitter!r}, "
                f"data={{'receiver': {{'community': {community_id!r}}}, 'type': 'community-submission'}}, "
                f"record=<draft {draft.id}>)"
            )
            try:
                rdm_service.review.create(
                    g.identity,
                    data={
                        "receiver": {"community": community_id},
                        "type": "community-submission",
                    },
                    record=draft._record,
                )
            except Exception as e:
                log("review_created", call, e)
                result.review_created = False
                return result
            log("review_created", call)
            result.review_created = True

            call = f"rdm_service.review.submit(identity={submitter!r}, id_={draft.id!r})"
            try:
                submitted = rdm_service.review.submit(g.identity, draft.id)
            except Exception as e:
                log("review_submitted", call, e)
                result.review_submitted = False
                return result
            if submitted.to_dict().get("status") != "submitted":
                log("review_submitted", call, f"unexpected result: {submitted.to_dict()}")
                result.review_submitted = False
                return result
            log("review_submitted", call)
            result.review_submitted = True

            approver = reviewer or submitter
            with current_runtime.login_user(approver):
                call = f"current_requests_service.execute_action(identity={approver!r}, id_={submitted.id!r}, action='accept')"
                try:
                    accepted = current_requests_service.execute_action(g.identity, submitted.id, "accept")
                except Exception as e:
                    log("review_accepted", call, e)
                    result.review_accepted = False
                    return result
                if accepted.to_dict().get("status") != "accepted":
                    log("review_accepted", call, f"unexpected result: {accepted.to_dict()}")
                    result.review_accepted = False
                    return result
                log("review_accepted", call)
                result.review_accepted = True
                call = f"rdm_service.read(identity='system', id_={draft.id!r})"
                try:
                    result.published = rdm_service.read(system_identity, draft.id).id
                except Exception as e:
                    log("published (post review_accepted read)", call, e)
                else:
                    log("published (post review_accepted read)", call)

        elif publish_directly:
            call = f"rdm_service.publish(identity={submitter!r}, id_={draft.id!r})"
            try:
                published = rdm_service.publish(g.identity, draft.id)
            except Exception as e:
                log("published_directly", call, e)
                result.published_directly = False
                return result
            if not published.to_dict().get("is_published"):
                log("published_directly", call, f"unexpected result: {published.to_dict()}")
                result.published_directly = False
                return result
            log("published_directly", call)
            result.published_directly = True
            result.published = published.id

        if readers and result.published:
            for reader_email in readers:
                with reader_identity(reader_email):
                    call = f"rdm_service.read(identity={reader_email!r}, id_={result.published!r})"
                    try:
                        rdm_service.read(g.identity, result.published)
                    except Exception as e:
                        log(f"published_read[{reader_email}]", call, e)
                        result.published_read[reader_email] = False
                    else:
                        log(f"published_read[{reader_email}]", call)
                        result.published_read[reader_email] = True

    return result


RESULT_BOOL_FIELDS = [
    f.name for f in fields(MakeRecordResult)
    if f.name not in ("draft", "published", "creator", "reviewer", "logs", "draft_read", "published_read")
]


def write_results_log(filename, results, settings):
    """Write the invenio calls made (with their parameters and outcome) for
    every result in this bucket, so the log serves as an execution trace,
    not just an error report."""
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        f.write("settings:\n")
        for key, value in settings.items():
            f.write(f"  {key}: {value}\n")
        f.write("\n")

        for result in results:
            f.write(f"creator={result.creator} reviewer={result.reviewer}\n")
            for entry in result.logs:
                f.write(f"  {entry}\n")
            f.write("\n")
    click.secho(f"log {filename}: written", fg="green")


def check_permissions(CHECKS, outdir):
    """Run each entry of CHECKS (a dict of make_record() kwargs, a "path"
    template and a "formatter") against a cartesian product of its set-valued
    arguments - except "readers", which make_record() already accepts as a
    whole collection and so is passed through unchanged to every call.

    "path" may reference any of the cartesian-expanded argument names as
    "{name}" placeholders. Results are gathered and split (grouped) by their
    rendered path - so combinations that render to the same path end up in
    one bucket - then the check's "formatter" turns each bucket into CSV rows.

    As in the previous implementation, a combination is skipped entirely
    (make_record() is never called for it) if its rendered path already
    exists on disk.
    """
    skipped = 0
    total = 0
    for check in tqdm(CHECKS, desc="checks"):
        check = dict(check)
        path_template = check.pop("path")
        formatter = check.pop("formatter")
        cartesian_keys = [key for key, value in check.items() if key != "readers" and isinstance(value, set)]
        in_path_keys = [key for key in cartesian_keys if f"{{{key}}}" in path_template]
        fixed_kwargs = {key: value for key, value in check.items() if key not in cartesian_keys}

        value_lists = [sorted(check[key]) for key in cartesian_keys]
        combos = list(itertools.product(*value_lists)) if cartesian_keys else [()]

        buckets = {}
        bucket_settings = {}
        for combo in tqdm(combos, desc="combinations", leave=False):
            total += 1
            combo_kwargs = dict(zip(cartesian_keys, combo))
            # strip "@demo.org" from any email-valued placeholders (e.g. a
            # fixed reviewer/creator) so paths read "individual/r/..." rather
            # than "individual/r@demo.org/...".
            rendered_path = path_template.format(**combo_kwargs).replace("@demo.org", "")
            full_path = os.path.join(outdir, rendered_path)
            if os.path.exists(full_path):
                skipped += 1
                continue

            kwargs = {**fixed_kwargs, **combo_kwargs}
            filter_func = kwargs.pop("filter", None)
            if filter_func and not filter_func(**kwargs):
                skipped += 1
                continue

            submitter = kwargs.pop("submitter")
            title = f"Test dataset ({rendered_path})"
            result = make_record(submitter, title, **kwargs)
            buckets.setdefault(rendered_path, []).append(result)
            bucket_settings.setdefault(rendered_path, {**fixed_kwargs, **{key: combo_kwargs[key] for key in in_path_keys}})

        for rendered_path, results in buckets.items():
            full_path = os.path.join(outdir, rendered_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", newline="") as f:
                csv.writer(f).writerows(formatter(results))
            click.secho(f"csv {full_path}: written", fg="green")

            log_path = full_path[: -len(".csv")] + ".log" if full_path.endswith(".csv") else full_path + ".log"
            write_results_log(log_path, results, bucket_settings[rendered_path])

    if skipped:
        click.secho(f"skipped {skipped}/{total} combinations (csv already exists)", fg="yellow")


def csv_to_markdown_table(csv_path):
    """Convert a CSV file to a markdown table."""
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return ""

    header = rows[0]
    data_rows = rows[1:]

    # Build markdown table
    lines = []

    # Header row
    lines.append("| " + " | ".join(header) + " |")

    # Separator row
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    # Data rows
    for row in data_rows:
        # Handle empty cells
        padded_row = row + [''] * (len(header) - len(row))
        lines.append("| " + " | ".join(padded_row[:len(header)]) + " |")

    return "\n".join(lines)


def generate_index_md(directory, title):
    """Generate index.md content for a directory."""
    csv_files = sorted(Path(directory).glob("*.csv"))
    log_files = sorted(Path(directory).glob("*.log"))

    lines = [f"# {title}\n", ""]

    for csv_path in csv_files:
        # Get corresponding log file (same name, different extension)
        log_path = csv_path.with_suffix(".log")
        has_log = log_path.exists()

        # Add section header
        csv_name = csv_path.name
        lines.append(f"## {csv_name}")
        lines.append("")

        # Add markdown table
        table = csv_to_markdown_table(csv_path)
        lines.append(table)
        lines.append("")

        # Add links (use .html for web compatibility)
        lines.append("**Links:**")
        lines.append(f"- [CSV file]({csv_name})")
        if has_log:
            log_name = log_path.name
            lines.append(f"- [Log file]({log_name})")
        else:
            lines.append("- Log file: not found")
        # Add link to index.html for this directory (for navigation from parent docs)
        lines.append("")

    return "\n".join(lines)


def create_index_files(outdir):
    """Create index.md files for each directory containing CSVs."""
    out_path = Path(outdir)

    # Find all directories containing CSV files
    csv_files = list(out_path.rglob("*.csv"))
    directories = sorted(set(csv_file.parent for csv_file in csv_files))

    click.echo(f"Found {len(directories)} directories with CSV files")

    for directory in directories:
        index_path = directory / "index.md"
        rel_parts = directory.relative_to(out_path).parts
        title = " - ".join([rel_parts[0].capitalize(), *rel_parts[1:]])
        content = generate_index_md(directory, title)

        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(content)

        csv_count = len(list(directory.glob("*.csv")))
        click.echo(f"Created {index_path} ({csv_count} CSVs)")


def create_html_output(indir, outdir):
    """Create HTML output directory structure for zensical build.

    Args:
        indir: Input directory containing communities/ and individual/ subdirectories
        outdir: Output directory where zensical site will be created
    """
    indir = Path(indir)
    outdir = Path(outdir)
    docs_dir = outdir / "docs"
    stylesheets_dir = docs_dir / "stylesheets"

    # 1. Create the outdir
    outdir.mkdir(parents=True, exist_ok=True)
    click.echo(f"Created {outdir}")

    # Collect navigation entries and links
    nav_entries = []
    individual_links = []
    communities_links = []

    individual_dir = indir / "individual"
    if individual_dir.exists():
        for subdir in sorted(individual_dir.iterdir()):
            if subdir.is_dir():
                rel_path = subdir.relative_to(indir)
                link_name = subdir.name.replace("_", " ").title()
                individual_links.append(f"[{link_name}]({rel_path}/index.html)")
                nav_entries.append(f'  {{ "I {link_name}" = "{rel_path}/index.html" }},')

    communities_dir = indir / "communities"
    if communities_dir.exists():
        for community in sorted(communities_dir.iterdir()):
            if community.is_dir():
                community_name = community.name.replace("-", " ").title()
                community_links = []
                has_entries = False
                for subdir in sorted(community.iterdir()):
                    if subdir.is_dir():
                        rel_path = subdir.relative_to(indir)
                        link_name = subdir.name.replace("_", " ").title()
                        community_links.append(f"  - [{link_name}]({rel_path}/index.html)")
                        nav_entries.append(f'  {{ "C {community_name}: {link_name}" = "{rel_path}/index.html" }},')
                        has_entries = True
                if community_links:
                    communities_links.append(f"### {community_name}\n\n" + "\n".join(community_links))

    # 2. Create outdir/zensical.toml with dynamic nav entries
    nav_content = "\n".join(nav_entries) if nav_entries else ""
    zensical_toml_content = ZENSICAL_TOML_TEMPLATE.replace("{{NAV_ENTRIES}}", nav_content)
    zensical_toml_path = outdir / "zensical.toml"
    with open(zensical_toml_path, 'w', encoding='utf-8') as f:
        f.write(zensical_toml_content)
    click.echo(f"Created {zensical_toml_path}")

    # 3. Create outdir/docs directory
    docs_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"Created {docs_dir}")

    # 4. Create outdir/docs/stylesheets/extra.css from constant
    stylesheets_dir.mkdir(parents=True, exist_ok=True)
    extra_css_path = stylesheets_dir / "extra.css"
    with open(extra_css_path, 'w', encoding='utf-8') as f:
        f.write(EXTRA_CSS)
    click.echo(f"Created {extra_css_path}")

    # 5. Create outdir/docs/index.md with all the checks
    index_content = INDEX_MD_TEMPLATE.format(
        individual_links="\n\n".join(individual_links) if individual_links else "No individual checks found",
        communities_links="\n\n".join(communities_links) if communities_links else "No community checks found"
    )

    index_path = docs_dir / "index.md"
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(index_content)
    click.echo(f"Created {index_path}")

    # 6. Deep copy indir/communities and indir/individual to outdir/docs
    if (indir / "communities").exists():
        shutil.copytree(indir / "communities", docs_dir / "communities", dirs_exist_ok=True)
        click.echo(f"Copied {indir / 'communities'} to {docs_dir / 'communities'}")

    if (indir / "individual").exists():
        shutil.copytree(indir / "individual", docs_dir / "individual", dirs_exist_ok=True)
        click.echo(f"Copied {indir / 'individual'} to {docs_dir / 'individual'}")

    # 7. Run zensical build
    import os
    from zensical import build as zensical_build
    original_cwd = os.getcwd()
    try:
        os.chdir(outdir)
        click.echo(f"Running zensical build in {outdir}...")
        zensical_build("zensical.toml", {"clean": True, "strict": False})
        click.echo("Build completed successfully!")
    finally:
        os.chdir(original_cwd)


setup_users()
setup_global_roles()
setup_communities()
setup_members()

outdir = sys.argv[1] if len(sys.argv) > 1 else "out"
check_permissions(CHECKS, outdir)
create_index_files(outdir)

# Create HTML output for zensical build
html_outdir = "html_output"
create_html_output(outdir, html_outdir)
