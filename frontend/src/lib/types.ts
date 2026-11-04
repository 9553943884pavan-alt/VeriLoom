export type Role = "admin" | "creator" | "reviewer";
export interface Membership {
  org_id: string;
  name: string;
  roles: Role[];
}
export interface Session {
  user_id: string;
  email: string;
  display_name: string;
  csrf_token: string;
  memberships: Membership[];
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
export interface Draft {
  draft_id: string;
  title: string;
  channel: string;
  state: string;
  working_copy_version: number;
  working_content?: string;
  review_id?: string | null;
  creator_user_id: string;
  current_revision_id?: string | null;
}
export interface Revision {
  revision_id: string;
  revision_number: number;
  content: string;
  title: string;
  review_id: string;
}
export interface Anchor {
  start_codepoint: number;
  end_codepoint: number;
  quote: string;
}
export interface Evidence {
  passage_id: string;
  source_ref: string;
  source_id: string;
  source_version: string;
  title: string;
  excerpt: string;
  effective_from: string;
  current_status: "approved" | "archived" | "expired";
}
export interface Finding {
  finding_id: string;
  category: string;
  severity: string;
  evidence_status: string;
  anchor: Anchor | null;
  explanation: string;
  suggested_edit: string | null;
  citations: Evidence[];
}
export interface Review {
  decision?: {
    decision_id: string;
    action: "request_changes" | "approve";
    comment: string | null;
    override_reason: string | null;
    decided_by_user_id: string;
    decided_at: string;
  } | null;
  review_id: string;
  draft_id: string;
  revision_id: string;
  revision_number: number;
  assigned_reviewer_user_id: string;
  state: string;
  coverage: "sufficient" | "partial" | "insufficient" | null;
  coverage_reason: string | null;
  review_version: number;
  findings: Finding[];
  failure: { message: string } | null;
}
export interface Source {
  source_id: string;
  title: string;
  category: string;
  latest_version_id: string | null;
  ingestion_status: string;
  approval_status: string;
  current_status: string;
  ingestion_error_code: string | null;
}
export interface SourceVersion {
  source_id: string;
  version_id: string;
  version_label: string;
  effective_from: string;
  effective_to: string | null;
  ingestion_status: string;
  approval_status: string;
  passage_count: number;
  ingestion_error_code: string | null;
  original_available: boolean;
}
export interface Question {
  turn_id: string;
  question: string;
  state: string;
  route: string | null;
  answer_status: string | null;
  answer: string | null;
  citations: Evidence[];
}
export interface Comment {
  comment_id: string;
  body: string;
  actor_user_id: string;
  created_at: string;
}
export interface Reviewer {
  user_id: string;
  display_name: string;
}
