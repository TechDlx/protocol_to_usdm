import type {
  AgentInputChange,
  AuditEntry,
  Codelist,
  Extraction,
  M11TemplateSection,
  MappingSuggestions,
  ParsedDocument,
  ReferenceValidation,
  ReviewOperation,
  ReviewState,
  RunDetail,
  SectionMapping,
  SheetLayout,
  SourceDocument,
  StudyCreate,
  StudySummary,
  TerminologyResolution,
  UsdmResult,
  WorkbookReport,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// FastAPI returns {detail: string} for handled errors and {detail: [...]} for validation errors.
function detailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)))
        .join("; ");
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // API data changes underneath open pages (runs, reviews); never answer from the browser cache.
  const res = await fetch(path, { cache: "no-store", ...init });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailMessage(body, `${res.status} ${res.statusText}`));
  return body as T;
}

const postJson = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const runUrl = (slug: string, runId: string) =>
  `/api/studies/${encodeURIComponent(slug)}/runs/${encodeURIComponent(runId)}`;

export const api = {
  listStudies: () => request<StudySummary[]>("/api/studies"),

  createStudy: (data: StudyCreate) =>
    request<StudySummary>("/api/studies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),

  createRun: (slug: string, sourceFilename: string) =>
    request<RunDetail>(`/api/studies/${encodeURIComponent(slug)}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_filename: sourceFilename }),
    }),

  getRun: (slug: string, runId: string) => request<RunDetail>(runUrl(slug, runId)),

  rerunIngestion: (slug: string, runId: string, force: boolean) =>
    request<RunDetail>(`${runUrl(slug, runId)}/ingest?force=${force}`, { method: "POST" }),

  startExtraction: (slug: string, runId: string, force: boolean, sheets?: string[]) =>
    request<RunDetail>(`${runUrl(slug, runId)}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force, sheets: sheets ?? null }),
    }),

  getExtraction: (slug: string, runId: string) =>
    request<Extraction>(`${runUrl(slug, runId)}/extraction`),

  getReferenceValidation: (slug: string, runId: string) =>
    request<ReferenceValidation>(`${runUrl(slug, runId)}/reference-validation`),

  getReview: (slug: string, runId: string) => request<ReviewState>(`${runUrl(slug, runId)}/review`),

  applyReviewOperations: (slug: string, runId: string, baseRevision: number, operations: ReviewOperation[]) =>
    request<ReviewState>(
      `${runUrl(slug, runId)}/review/operations`,
      postJson({ base_revision: baseRevision, operations }),
    ),

  confirmReview: (slug: string, runId: string, baseRevision: number) =>
    request<ReviewState>(`${runUrl(slug, runId)}/review/confirm`, postJson({ base_revision: baseRevision })),

  restartReview: (slug: string, runId: string, baseRevision: number) =>
    request<ReviewState>(`${runUrl(slug, runId)}/review/restart`, postJson({ base_revision: baseRevision })),

  getReviewAudit: (slug: string, runId: string, limit = 100) =>
    request<AuditEntry[]>(`${runUrl(slug, runId)}/review/audit?limit=${limit}`),

  sourceHighlightUrl: (slug: string, runId: string, page: number, quote: string | null) =>
    `${runUrl(slug, runId)}/source-highlight?page=${page}${quote ? `&quote=${encodeURIComponent(quote.slice(0, 1500))}` : ""}`,

  generateWorkbook: (slug: string, runId: string, force: boolean) =>
    request<WorkbookReport>(`${runUrl(slug, runId)}/workbook?force=${force}`, { method: "POST" }),

  getWorkbookReport: (slug: string, runId: string) => request<WorkbookReport>(`${runUrl(slug, runId)}/workbook`),

  workbookDownloadUrl: (slug: string, runId: string) => `${runUrl(slug, runId)}/workbook/download`,

  // Stage C runs in the background; poll the run for its "usdm" stage.
  generateUsdm: (slug: string, runId: string, force: boolean) =>
    request<RunDetail>(`${runUrl(slug, runId)}/usdm?force=${force}`, { method: "POST" }),

  getUsdm: (slug: string, runId: string) => request<UsdmResult>(`${runUrl(slug, runId)}/usdm`),

  usdmDownloadUrl: (slug: string, runId: string) => `${runUrl(slug, runId)}/usdm/download`,

  usdmReportDownloadUrl: (slug: string, runId: string) => `${runUrl(slug, runId)}/usdm/report/download`,

  getLayouts: () => request<SheetLayout[]>("/api/workbook/layouts"),

  getCodelist: (klass: string, attribute: string, q: string) =>
    request<Codelist>(
      `/api/terminology/codelist?klass=${encodeURIComponent(klass)}&attribute=${encodeURIComponent(attribute)}&q=${encodeURIComponent(q)}`,
    ),

  getBiomedicalConcepts: (q: string) =>
    request<Codelist>(`/api/terminology/biomedical-concepts?q=${encodeURIComponent(q)}`),

  resolveBiomedicalConcept: (phrase: string) =>
    request<TerminologyResolution | null>(
      "/api/terminology/resolve-biomedical-concept",
      postJson({ klass: "", attribute: "", phrase }),
    ),

  resolveTerm: (klass: string, attribute: string, phrase: string) =>
    request<TerminologyResolution | null>("/api/terminology/resolve", postJson({ klass, attribute, phrase })),

  getDocument: (slug: string, runId: string) =>
    request<ParsedDocument>(`${runUrl(slug, runId)}/document`),

  getSectionMapping: (slug: string, runId: string) =>
    request<SectionMapping>(`${runUrl(slug, runId)}/section-mapping`),

  getM11Template: () => request<M11TemplateSection[]>("/api/m11/template"),

  /** Map a section by hand: an M11 number, or excluded (not protocol content). */
  setSectionMapping: (
    slug: string,
    runId: string,
    sectionId: string,
    body: { m11_number?: string; excluded?: boolean; source?: string },
  ) =>
    request<SectionMapping>(`${runUrl(slug, runId)}/section-mapping/${encodeURIComponent(sectionId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /** Map a section to a further M11 section as well, keeping its current mapping. */
  addSectionMapping: (slug: string, runId: string, sectionId: string, m11Number: string, source?: string) =>
    request<SectionMapping>(`${runUrl(slug, runId)}/section-mapping/${encodeURIComponent(sectionId)}/also`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ m11_number: m11Number, source }),
    }),

  /** Ask Claude for mapping suggestions (costs money; changes nothing until accepted). */
  suggestMappings: (slug: string, runId: string, scope: "flagged" | "all", sectionIds: string[] = []) =>
    request<MappingSuggestions>(`${runUrl(slug, runId)}/section-mapping/suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, section_ids: sectionIds }),
    }),

  getMappingSuggestions: (slug: string, runId: string) =>
    request<MappingSuggestions>(`${runUrl(slug, runId)}/section-mapping/suggestions`),

  removeSectionMapping: (slug: string, runId: string, sectionId: string, m11Number: string) =>
    request<SectionMapping>(
      `${runUrl(slug, runId)}/section-mapping/${encodeURIComponent(sectionId)}/also/${encodeURIComponent(m11Number)}`,
      { method: "DELETE" },
    ),

  clearSectionMapping: (slug: string, runId: string, sectionId: string) =>
    request<SectionMapping>(`${runUrl(slug, runId)}/section-mapping/${encodeURIComponent(sectionId)}`, {
      method: "DELETE",
    }),

  /** Move where a section starts; pages shift between it and the previous section. */
  setSectionStartPage: (slug: string, runId: string, sectionId: string, startPage: number) =>
    request<SectionMapping>(`${runUrl(slug, runId)}/sections/${encodeURIComponent(sectionId)}/start-page`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_page: startPage }),
    }),

  clearSectionStartPage: (slug: string, runId: string, sectionId: string) =>
    request<SectionMapping>(`${runUrl(slug, runId)}/sections/${encodeURIComponent(sectionId)}/start-page`, {
      method: "DELETE",
    }),

  getExtractionInputChanges: (slug: string, runId: string) =>
    request<AgentInputChange[]>(`${runUrl(slug, runId)}/extraction/input-changes`),

  // image_path is "page_images/page-0001.png"; the API serves the file name under /pages/.
  pageImageUrl: (slug: string, runId: string, imagePath: string) =>
    `${runUrl(slug, runId)}/pages/${encodeURIComponent(imagePath.split("/").pop() ?? "")}`,

  sourceUrl: (slug: string, filename: string) =>
    `/api/studies/${encodeURIComponent(slug)}/sources/${encodeURIComponent(filename)}`,

  // XHR rather than fetch: fetch cannot report upload progress, and protocols can be large.
  uploadSource: (slug: string, file: File, onProgress: (fraction: number) => void) =>
    new Promise<SourceDocument>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/studies/${encodeURIComponent(slug)}/sources`);
      xhr.responseType = "json";
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as SourceDocument);
        else reject(new ApiError(xhr.status, detailMessage(xhr.response, `Upload failed (${xhr.status})`)));
      };
      xhr.onerror = () => reject(new ApiError(0, "Network error — is the backend running on :8000?"));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    }),
};
