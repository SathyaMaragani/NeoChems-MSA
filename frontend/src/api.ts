/** Where the RamChems API lives.
 *
 *  Port 8434 rather than 8000: 8000 is the default every framework reaches for,
 *  so on a machine running more than one project it is contended, and a service
 *  answering there may belong to something else entirely. The Postgres port is
 *  5434 for the same reason. Override with VITE_API_BASE (see .env.example).
 *
 *  Exported so nothing else hardcodes a URL - a second copy is how the two
 *  drift apart and the UI tells you to check a port it is not using.
 */
export const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8434'

const BASE = API_BASE

/** Backend is unreachable (not running, wrong port, CORS refused). */
export class BackendDownError extends Error {
  constructor() {
    super(`Cannot reach the backend at ${BASE}`)
    this.name = 'BackendDownError'
  }
}

/** Backend answered, but rejected the request (400 invalid SMILES, 404 no match). */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  // FastAPI validation errors arrive as a list of {loc, msg, ...}
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string }
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : undefined
        return field ? `${field}: ${item.msg}` : item.msg
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal })
  } catch (err) {
    // fetch rejects with TypeError for network failure and AbortError on timeout;
    // neither means the backend said no, so both read as "not reachable".
    throw new BackendDownError()
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json())?.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(
      response.status,
      detailToMessage(detail, `${response.status} ${response.statusText}`),
    )
  }
  return response.json() as Promise<T>
}

function post<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  return request<T>(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    timeoutMs,
  )
}

// --- molecules -------------------------------------------------------------

export type Molecule = {
  id: number
  canonical_smiles: string
  original_smiles: string
  inchikey: string
  source: string
  is_mineral_salt: boolean
  molecular_weight: number
  created_at: string
  image_png_base64?: string | null
}

export type Representation = {
  input_smiles: string
  canonical_smiles: string
  smiles_as_given_canonical: string
  inchikey: string
  molecular_weight: number
  morgan_fingerprint_on_bits: number[]
  morgan_radius: number
  morgan_n_bits: number
  image_png_base64: string
}

export const represent = (smiles: string) =>
  post<Representation>('/molecules/represent', { smiles })

export const getMolecule = (id: number) => request<Molecule>(`/molecules/${id}`)

export type MoleculeStats = { total: number; mineral_salts: number; sources: number }

export const moleculeStats = () => request<MoleculeStats>('/molecules/stats')

export type Resolution = {
  query: string
  canonical_smiles: string
  /** "smiles" when the input already parsed; "pubchem" when a name was looked up. */
  source: 'smiles' | 'pubchem'
  matched_name: string | null
}

/** Accepts a SMILES or a compound name. Names are the common case for people
 *  who are not thinking in SMILES, and used to fail as an unparseable string. */
export const resolveQuery = (query: string) =>
  post<Resolution>('/molecules/resolve', { query }, 20_000)

// --- search ----------------------------------------------------------------

export type SimilarityHit = Molecule & { tanimoto: number }

export const exactSearch = (smiles: string) =>
  post<Molecule>('/search/exact', { smiles })

export const similaritySearch = (
  smiles: string,
  top_n: number,
  min_similarity: number,
) =>
  post<{ query_canonical_smiles: string; count: number; results: SimilarityHit[] }>(
    '/search/similarity',
    { smiles, top_n, min_similarity },
  )

export const substructureSearch = (smiles_pattern: string, top_n: number) =>
  post<{ query_pattern: string; count: number; results: Molecule[] }>(
    '/search/substructure',
    { smiles_pattern, top_n },
  )

// --- retrosynthesis --------------------------------------------------------

/** How a condition came to be known. Never blurred in the UI. */
export type EvidenceLevel =
  | 'experimental'
  | 'similar_experimental'
  | 'predicted'
  | 'unavailable'

export type ConditionValue = {
  value: number | string
  unit?: string
  normalized_value?: number
  normalized_unit?: string
  minimum?: number
  maximum?: number
  evidence_level?: EvidenceLevel
  /** Populated ONLY for predictions. Experimental values never carry one. */
  confidence?: number
  original_text?: string
  observation_count?: number
}

export type ChemicalEntity = {
  name?: string
  smiles?: string
  role?: string
  original_text?: string
}

export type Provenance = {
  source_type?: string
  source_id?: string
  dataset_name?: string
  title?: string
  authors?: string[]
  journal?: string
  year?: number
  doi?: string
  patent_number?: string
  /** Only present when the source supplied one; never built from a DOI. */
  url?: string
  license?: string
}

export type ReactionConditions = {
  evidence_level: EvidenceLevel
  reagents?: ChemicalEntity[]
  catalysts?: ChemicalEntity[]
  solvents?: ChemicalEntity[]
  temperature?: ConditionValue
  time?: ConditionValue
  pressure?: ConditionValue
  yield?: ConditionValue
  workup?: string[]
  notes?: string
}

export type Precedent = {
  reaction_id: string
  reaction_smiles?: string
  conditions?: ReactionConditions
  provenance?: Provenance
  /** Chemical similarity - NOT a probability the reaction will work. */
  similarity?: number
  match_type?: string
}

export type ReactionEvidence = {
  evidence_level: EvidenceLevel
  conditions?: ReactionConditions
  direct_precedents?: Precedent[]
  similar_precedents?: Precedent[]
  precedent_count?: number
  provider?: string
  dataset_version?: string
  cached?: boolean
  reason?: string
}

export type EvidenceSummary = {
  steps: number
  steps_with_experimental_evidence: number
  steps_with_similar_evidence: number
  steps_predicted: number
  steps_without_evidence: number
  evidence_coverage: number
  /** Distinct source records (DOI / patent / record id) behind the matched
   *  precedents. NOT a count of the literature. */
  distinct_sources: number
}

export type ValidationStatus = 
  | 'MATCH' 
  | 'PARTIAL_MATCH' 
  | 'MISMATCH' 
  | 'MODEL_OUTPUT_INVALID'
  | 'MODEL_UNAVAILABLE'
  | 'VALIDATION_ERROR'

export type ValidationResult = {
  status: ValidationStatus
  interpretation?: string
  model?: string
  predicted_products?: string[]
  inference_time_ms?: number
  error?: string
}

export type Reaction = {
  reactants: RouteNode[]
  template_used: number | null
  template_hash?: string | null
  template_smarts: string | null
  /** Times this template appears in the USPTO template library. A library
   *  count - not successful experiments, not a yield, not a probability. */
  template_occurrence?: number | null
  score: number | null
  reaction_smiles: string
  classification: string | null
  evidence?: ReactionEvidence
  structural_validation?: ValidationResult
  forward_validation?: ValidationResult
  assessment?: ReactionAssessment
}

export type AssessmentSummary = 
  | 'STRONG_SUPPORT'
  | 'SUPPORTED'
  | 'REVIEW_REQUIRED'
  | 'INSUFFICIENT_EVIDENCE'

export type ReactionAssessment = {
  summary: AssessmentSummary
  label: string
  interpretation: string
  flags: string[]
  route_score_affected: boolean
}

export type RouteNode = {
  molecule_smiles: string
  is_stock_available: boolean
  reactions: Reaction[]
}

export type RouteAssessment = {
  summary: AssessmentSummary
  label: string
  critical_steps: { reaction_smiles: string; reason: string; flags: string[] }[]
  flags: string[]
  route_score_affected: boolean
}

export type Route = {
  route_id: number
  evidence_summary?: EvidenceSummary
  assessment?: RouteAssessment
  state_score: number | null
  scores: Record<string, number>
  number_of_reactions: number
  tree: RouteNode
  image_png_base64: string | null
}

export type Plan = {
  target_smiles: string
  is_solved: boolean
  search_time_seconds: number
  iterations_used: number
  iteration_limit: number
  solved_routes_found: number
  routes_returned: number
  routes: Route[]
}

/** A 500-iteration search takes ~95s server-side, so allow well beyond that. */
export const planRoutes = (
  smiles: string,
  top_n: number,
  iteration_limit: number,
  include_conditions = false,
  include_validation = false,
) =>
  post<Plan>(
    '/retrosynthesis/plan',
    { smiles, top_n, iteration_limit, include_images: true, include_conditions, include_validation },
    300_000,
  )

export type EvidenceStatus = {
  provider: string
  provider_display_name: string
  available: boolean
  /** Size of the indexed corpus that is actually searched. */
  indexed_reactions?: number
  coverage_note?: string
  dataset_version?: string
  data_license?: string
  prediction_model: { provider: string; available: boolean; note?: string }
}

export const evidenceStatus = () =>
  request<EvidenceStatus>('/retrosynthesis/evidence/status', undefined, 8_000)

export type Health = {
  status: string
  model_loaded: boolean
  load_time_seconds: number
  max_iteration_limit: number
}

export const retroHealth = () => request<Health>('/retrosynthesis/health', undefined, 5_000)

// --- QSAR property prediction ----------------------------------------------

export type CoverageRow = {
  alpha: number
  nominal: number
  /** Measured on held-out data, not the nominal label. */
  empirical: number
  /** Wilson 95% CI: coverage is itself an estimate from n_test molecules. */
  empirical_ci_95: number[]
  n_test: number
  mean_width: number
}

export type PropertyModel = {
  model: string
  description: string
  test_rmse: number
  test_mae: number
  test_r2: number
  coverage: CoverageRow[]
}

export type PropertySpec = {
  property: string
  units: string
  description: string
  dataset: string
  default_model: string
  task_type: string
  calibrated_alphas: number[]
  models: PropertyModel[]
}

export type PredictionInterval = {
  lower: number
  upper: number
  alpha: number
  nominal_coverage: number
  method: string
  empirical_coverage: number
  empirical_coverage_ci_95: number[]
  n_calibration: number
  n_test: number
  note: string
}

export type Prediction = {
  property: string
  predicted_value: number
  units: string
  model_used: string
  model_performance: {
    test_rmse: number
    test_mae: number
    test_r2: number
    split: string
    note: string
  }
  prediction_interval: PredictionInterval
  applicability: {
    max_train_similarity: number
    structurally_familiar: boolean
    threshold: number
    nearest_training_smiles: string[]
    note: string
  }
}

export const listProperties = () =>
  request<{ properties: PropertySpec[] }>('/predict/properties')

export const predictProperty = (
  smiles: string,
  property: string,
  model: string | undefined,
  alpha: number,
) => post<Prediction>('/predict/property', { smiles, property, model, alpha })
