"""
Pydantic models for knowledge extraction JSON schema.
Enforces Shatalov constraints and validates extraction output.
"""

from typing import List, Optional, Literal, Dict
from pydantic import BaseModel, Field, field_validator


class Community(BaseModel):
    """Thematic cluster of related concepts."""
    community_id: str
    size: int
    concept_refs: List[str]
    top_concept_refs: List[str] = Field(default_factory=list)
    internal_edge_weight_sum: float


class CommunityStats(BaseModel):
    """Summary statistics for the whole clustering run."""
    community_count: int
    singleton_communities: int
    max_community_size: int
    modularity: float
    concepts_clustered_ratio: float


class CommunityAssignment(BaseModel):
    """Refined community assignment with score."""
    community_id: str
    score: float

class TopicCommunity(BaseModel):
    """
    Pedagogical representation of a knowledge cluster (topic).
    """
    community_id: str
    label: str
    short_summary: str = ""
    concept_refs: List[str] = []
    meaning_unit_refs: List[str] = []
    card_refs: List[str] = []
    
    # Quality Scores (Refined)
    cohesion_score: float = 0.0  # How tightly concepts are related
    consistency_score: float = 0.0    # How exclusive concepts are to this topic
    coverage_score: float = 0.0  # How well cards cover the concepts
    
    # Pedagogical Status
    pedagogical_status: Literal["standard", "orphan", "hidden"] = "standard"
    
    bridge_concept_refs: List[str] = [] # Concepts that link to other topics


class SourceSpan(BaseModel):
    """Character position in source text."""
    start_char: int
    end_char: int


class Relation(BaseModel):
    """Single relationship between concepts/units."""
    rel: str
    from_: str = Field(alias="from")
    to: str
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class Seeds(BaseModel):
    """Minimal information to reconstruct understanding."""
    rule_or_core: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    example: Optional[str] = None
    mistake_and_fix: Optional[str] = None


class MeaningUnit(BaseModel):
    """Atomic unit of educational content."""
    unit_id: str
    type: Literal[
        "definition",
        "claim_theorem",
        "method_procedure",
        "example",
        "consequence",
        "warning_common_mistake",
        "background"
    ]
    source_span: SourceSpan
    summary: str
    key_terms: List[str]
    symbols: List[str] = Field(default_factory=list)
    concept_refs: List[str] = Field(default_factory=list)
    relations: List[Relation] = Field(default_factory=list)
    seeds: Seeds
    community_id: Optional[str] = None
    community_score: float = 0.0
    secondary_communities: List[CommunityAssignment] = []


class Concept(BaseModel):
    """Extracted concept with metadata."""
    concept_id: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    short_def: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    prerequisites: List[str] = Field(default_factory=list)
    prerequisite_sources: List[str] = Field(default_factory=list) # e.g. "derived_relation", "llm_extracted"
    inferred_prerequisites: List[str] = Field(default_factory=list) # weak/topic-based prerequisites
    contrasts: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    cluster_id: Optional[str] = None

    # Pedagogical hierarchy (set by abstraction_layer + hierarchy_layer)
    hierarchy_level: Optional[int] = None         # Same as abstraction_level; kept for UI back-compat
    abstraction_level: Optional[int] = None       # 1=concrete, num_levels=course goal
    abstraction_depth: Optional[int] = None       # raw longest-path depth in abstraction DAG
    abstraction_level_label: Optional[str] = None
    usage_level: Optional[int] = None             # frequency-based banding (secondary)
    hierarchy_context: Optional[Dict[str, List[str]]] = None  # ancestors/siblings/descendants
    bridge_score: Optional[float] = None
    bottleneck_score: Optional[float] = None


class RelationGraph(BaseModel):
    """Global relationship in knowledge graph."""
    rel_id: str
    type: Literal[
        "prerequisite",
        "contrasts_with",
        "used_for",
        "example_of",
        "counterexample_of",
        "causes",
        "part_of",
        "is_a",
        "related"
    ]
    from_: str = Field(alias="from")
    to: str
    label: str
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)


class MiniLink(BaseModel):
    """Compact link for skeleton card visualization."""
    from_: str = Field(alias="from")
    rel: str
    to: str
    label: str


class SeedPack(BaseModel):
    """Reconstruction seeds for a block."""
    rule_plus_conditions: Optional[str] = None
    procedure_steps: List[str] = Field(default_factory=list)
    one_strong_example: Optional[str] = None
    one_common_mistake: Optional[str] = None


class Block(BaseModel):
    """Single block in Shatalov skeleton card."""
    block_id: str
    title: str
    anchors: List[str] = Field(min_length=1, max_length=3)
    unit_refs: List[str] = Field(default_factory=list)
    concept_refs: List[str] = Field(default_factory=list)
    mini_links: List[MiniLink] = Field(default_factory=list)
    seed_pack: SeedPack

    @field_validator('anchors')
    @classmethod
    def validate_anchors(cls, v):
        """Ensure anchors are short (2-5 words)."""
        for anchor in v:
            word_count = len(anchor.split())
            if word_count > 5:
                raise ValueError(f"Anchor '{anchor}' too long ({word_count} words, max 5)")
        return v


class SkeletonCard(BaseModel):
    """Shatalov-style skeleton card for a topic."""
    card_id: str
    topic: str
    blocks: List[Block] = Field(min_length=1, max_length=7)
    community_id: Optional[str] = None
    community_score: float = 0.0
    secondary_communities: List[CommunityAssignment] = []
    topic_distribution: Dict[str, float] = {} # {community_id: weight}
    relevance_score: float = 0.0

    @field_validator('blocks')
    @classmethod
    def validate_blocks(cls, v):
        """Enforce Shatalov constraint: 5-7 blocks per card."""
        if not (5 <= len(v) <= 7):
            raise ValueError(f"Skeleton card must have 5-7 blocks, got {len(v)}")
        return v


class CoverageEstimate(BaseModel):
    """Estimated coverage of extracted knowledge."""
    major_concepts: int
    major_methods: int
    major_mistakes: int


class QualityChecks(BaseModel):
    """Validation and quality metrics."""
    shatalov_constraint_ok: bool
    num_blocks_per_card: List[int]
    missing_prereqs_warnings: List[str]
    knowledge_islands: List[str]
    top_contrasts: List[str]
    coverage_estimate: CoverageEstimate


class TopicDependency(BaseModel):
    """Directed relationship between topics."""
    from_topic_id: str
    to_topic_id: str
    dependency_type: Literal["prerequisite", "successor", "related"]
    strength: float = Field(ge=0.0, le=1.0)
    evidence_concept_refs: List[str] = Field(default_factory=list)
    evidence_relation_refs: List[str] = Field(default_factory=list)


class PrerequisiteChain(BaseModel):
    """Sequential path of concepts building on each other."""
    chain_id: str
    label: str
    chain_type: Literal["linear", "branching", "convergent"] = "linear"
    concept_refs: List[str]
    start_concept_id: str
    end_concept_id: str
    path_score: float = 0.0


class BridgeConcept(BaseModel):
    """Concept that connects multiple topics."""
    concept_id: str
    topic_refs: List[str]
    bridge_score: float


class BottleneckConcept(BaseModel):
    """Critical foundation concept for many downstream nodes."""
    concept_id: str
    dependency_reach: int
    downstream_topic_count: int = 0
    bottleneck_score: float


class LearningPath(BaseModel):
    """Suggested pedagogical route through topics and cards."""
    path_id: str
    label: str
    description: str = ""
    path_type: Literal["intro", "prereq_check", "transition", "deep_dive", "abstraction_climb"] = "deep_dive"
    topic_refs: List[str] = Field(default_factory=list)
    concept_refs: List[str] = Field(default_factory=list)
    card_refs: List[str] = Field(default_factory=list)
    entry_points: List[str] = Field(default_factory=list)
    exit_points: List[str] = Field(default_factory=list)
    path_score: float = 0.0
    estimated_steps: int = 0
    validation_status: str = "unknown" # "valid", "degraded", "invalid"
    validation_notes: List[str] = Field(default_factory=list)


class ReasoningStats(BaseModel):
    """Summary metrics for the reasoning layer."""
    topic_dependency_count: int = 0
    bridge_concept_count: int = 0
    bottleneck_concept_count: int = 0
    learning_path_count: int = 0
    chain_count: int = 0
    cycle_count_before_cleanup: int = 0
    cycle_count_after_cleanup: int = 0
    bridge_only_count: int = 0
    bottleneck_only_count: int = 0
    overlap_count: int = 0

class IntegrityReport(BaseModel):
    """Report on referential integrity cleanup."""
    dangling_card_concept_refs_removed: int = 0
    dangling_unit_concept_refs_removed: int = 0
    dangling_path_refs_removed: int = 0
    empty_cards_after_cleanup: int = 0
    warnings: List[str] = Field(default_factory=list)

class ReasoningValidation(BaseModel):
    """Sanity checks for pedagogical logic."""
    topic_self_loops: int = 0
    concept_self_prerequisites: int = 0
    topic_cycles_after_cleanup: int = 0
    suspicious_dependencies: int = 0
    path_validation_status: str = "unknown"

class ValidationReport(BaseModel):
    """Unified quality gate report."""
    status: Literal["pass", "pass_with_warnings", "fail"] = "pass"
    critical_errors: int = 0
    warnings: int = 0
    integrity: IntegrityReport = Field(default_factory=IntegrityReport)
    reasoning: ReasoningValidation = Field(default_factory=ReasoningValidation)


class Meta(BaseModel):
    """Metadata about the extraction."""
    course_title: Optional[str] = None
    chapter_title: Optional[str] = None
    domain: str
    language: str
    compression_notes: str


class ExtractionOutput(BaseModel):
    """Complete extraction output schema."""
    meta: Meta
    meaning_units: List[MeaningUnit]
    concepts: List[Concept]
    relations_graph: List[RelationGraph]
    relation_views: Dict[str, List[Dict]] = Field(default_factory=dict)
    abstraction_report: Dict = Field(default_factory=dict)
    skeleton_cards: List[SkeletonCard]
    quality_checks: QualityChecks
    communities: List[Community] = Field(default_factory=list)
    topic_communities: List[TopicCommunity] = Field(default_factory=list)
    community_stats: Optional[CommunityStats] = None

    # Reasoning Layer
    topic_dependencies: List[TopicDependency] = Field(default_factory=list)
    prerequisite_chains: List[PrerequisiteChain] = Field(default_factory=list)
    learning_paths: List[LearningPath] = Field(default_factory=list)
    bridge_concepts: List[BridgeConcept] = Field(default_factory=list)
    bottleneck_concepts: List[BottleneckConcept] = Field(default_factory=list)
    reasoning_stats: Optional[ReasoningStats] = None

    # Hardening & Validation (Step 3.5)
    validation_report: Optional[ValidationReport] = None
