package statelessness

import (
	"fmt"
	"maps"
	"math"
	"math/big"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"unicode/utf8"
)

// AssuranceManifest declares independent adequacy requirements; it does not
// generate inputs or establish that declared environment assumptions hold.
type AssuranceProperty struct{ ID, Phase string }
type AssuranceFamily struct {
	Name     string
	Excluded bool
	Reason   string
}
type AssuranceGoal struct{ ID, Scope string }
type AssuranceProvenance struct{ Manifest, Model, Properties string }
type AssuranceManifest struct {
	Version         uint32
	Model           string
	Properties      []AssuranceProperty
	Families        []AssuranceFamily
	Bounds          map[string]string
	Goals           []AssuranceGoal
	RequiredOutcome string
	Provenance      AssuranceProvenance
}

// AddDomain copies declared environmental assumptions into the manifest. It
// does not claim the domain was exercised or that its assumptions hold.
func (m *AssuranceManifest) AddDomain(domain DomainDescriptor) error {
	if m == nil || domain.MaxEntries < 0 || !utf8.ValidString(domain.Name) || !utf8.ValidString(domain.Assumptions) || strings.TrimSpace(domain.Name) == "" || strings.TrimSpace(domain.Assumptions) == "" {
		return &ModelError{"invalid assurance domain declaration"}
	}
	// Detach existing bounds so a manifest copied by value does not mutate the
	// other copy's caller-owned declarations.
	bounds := maps.Clone(m.Bounds)
	if bounds == nil {
		bounds = make(map[string]string)
	}
	bounds["domain."+domain.Name+".assumptions"] = domain.Assumptions
	bounds["domain."+domain.Name+".max_entries"] = strconv.Itoa(domain.MaxEntries)
	m.Bounds = bounds
	return nil
}

// Require declares an independently expected property; validation and actual
// observed checks remain separate.
func (m *AssuranceManifest) Require(id, phase string) error {
	if m == nil || strings.TrimSpace(id) == "" || !utf8.ValidString(id) || (phase != "state" && phase != "transition") {
		return &ModelError{"invalid required property declaration"}
	}
	m.Properties = append(slices.Clone(m.Properties), AssuranceProperty{ID: id, Phase: phase})
	return nil
}

func (m AssuranceManifest) Validate() error {
	valid := func(s string) bool { return utf8.ValidString(s) }
	for _, v := range []string{m.Model, m.Provenance.Manifest, m.Provenance.Model, m.Provenance.Properties} {
		if !valid(v) {
			return &ModelError{"assurance identity/provenance must be valid UTF-8"}
		}
	}
	for _, p := range m.Properties {
		if !valid(p.ID) || !valid(p.Phase) {
			return &ModelError{"assurance property must be valid UTF-8"}
		}
	}
	for _, f := range m.Families {
		if !valid(f.Name) || !valid(f.Reason) {
			return &ModelError{"assurance family must be valid UTF-8"}
		}
	}
	for _, g := range m.Goals {
		if !valid(g.ID) || !valid(g.Scope) {
			return &ModelError{"assurance goal must be valid UTF-8"}
		}
	}
	for k, v := range m.Bounds {
		if !valid(k) || !valid(v) {
			return &ModelError{"assurance bounds must be valid UTF-8"}
		}
	}
	if m.Version != 1 {
		return &ModelError{"unsupported assurance manifest version"}
	}
	for _, v := range []string{m.Model, m.Provenance.Manifest, m.Provenance.Model, m.Provenance.Properties} {
		if strings.TrimSpace(v) == "" {
			return &ModelError{"assurance identity/provenance must not be empty"}
		}
	}
	if m.RequiredOutcome != "graph_exhausted" && m.RequiredOutcome != "completed" {
		return &ModelError{"invalid required search outcome"}
	}
	properties := map[AssuranceProperty]bool{}
	for _, p := range m.Properties {
		if strings.TrimSpace(p.ID) == "" || (p.Phase != "state" && p.Phase != "transition") || properties[p] {
			return &ModelError{"empty or duplicate required property"}
		}
		properties[p] = true
	}
	families := map[string]bool{}
	for _, f := range m.Families {
		if strings.TrimSpace(f.Name) == "" || families[f.Name] || (f.Excluded && strings.TrimSpace(f.Reason) == "") || (!f.Excluded && f.Reason != "") {
			return &ModelError{"invalid assurance input family"}
		}
		families[f.Name] = true
	}
	goals := map[string]bool{}
	for _, g := range m.Goals {
		if strings.TrimSpace(g.ID) == "" || goals[g.ID] || (g.Scope != "transition" && g.Scope != "history") {
			return &ModelError{"invalid assurance cover goal"}
		}
		goals[g.ID] = true
	}
	for k, v := range m.Bounds {
		if strings.TrimSpace(k) == "" || strings.TrimSpace(v) == "" {
			return &ModelError{"environment bound names and assumptions must not be empty"}
		}
	}
	return nil
}

type AssuranceCheckCounts struct{ Passed, Failed, Skipped, Missing uint64 }
type AssuranceHits struct{ Families, Goals []string }
type AssuranceEvidence struct {
	Manifest                                    AssuranceManifest
	Metadata                                    Metadata
	Outcome, Termination                        string
	CheckedTransitions, UnclassifiedTransitions uint64
	Properties                                  map[AssuranceProperty]AssuranceCheckCounts
	Families, Goals                             map[string]bool
	Errors                                      []string
	Budgets                                     map[string]string
	merged                                      *assuranceMergeProof
}
type AssuranceAssessment struct {
	Adequate   bool
	Reasons    []string
	Confidence string
}

// AssuranceCollector observes already checked batches; its callbacks never run
// the reducer. A failed coverage callback contributes no partial hits.
type AssuranceCollector[S, I, O any] struct {
	manifest                        AssuranceManifest
	metadata                        Metadata
	properties                      map[AssuranceProperty]AssuranceCheckCounts
	families, goals                 map[string]bool
	declaredFamilies, declaredGoals map[string]bool
	needsHistory                    bool
	errors                          map[string]bool
	transitions, unclassified       uint64
	coverage                        func(S, I, Transition[S, O], []I) (AssuranceHits, error)
	limits                          AssuranceObservationLimits
	limitExceeded                   bool
	busy                            atomic.Bool
	rejected                        atomic.Bool
}

func (a *AssuranceCollector[S, I, O]) enter() bool {
	if a.busy.CompareAndSwap(false, true) {
		return true
	}
	a.rejected.Store(true)
	return false
}

// AssuranceObservationLimits bound retained collector metadata, not allocations
// performed inside caller callbacks. Exceeding a limit invalidates adequacy.
type AssuranceObservationLimits struct {
	MaxProperties, MaxErrors, MaxBatchChecks, MaxCoverageHits, MaxTextBytes int
	MaxFamilies, MaxGoals, MaxBounds, MaxManifestBytes                      int
}

func DefaultAssuranceObservationLimits() AssuranceObservationLimits {
	return AssuranceObservationLimits{MaxProperties: 65536, MaxErrors: 1024, MaxBatchChecks: 65536, MaxCoverageHits: 65536, MaxTextBytes: 4096, MaxFamilies: 65536, MaxGoals: 65536, MaxBounds: 65536, MaxManifestBytes: 8 << 20}
}

func cloneAssuranceManifest(m AssuranceManifest) AssuranceManifest {
	m.Properties = slices.Clone(m.Properties)
	m.Families = slices.Clone(m.Families)
	m.Goals = slices.Clone(m.Goals)
	b := make(map[string]string, len(m.Bounds))
	for k, v := range m.Bounds {
		b[k] = v
	}
	m.Bounds = b
	return m
}
func NewAssuranceCollector[S, I, O any](m AssuranceManifest, metadata Metadata, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error)) (*AssuranceCollector[S, I, O], error) {
	return NewAssuranceCollectorWithLimits(m, metadata, coverage, DefaultAssuranceObservationLimits())
}
func NewAssuranceCollectorWithLimits[S, I, O any](m AssuranceManifest, metadata Metadata, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), limits AssuranceObservationLimits) (*AssuranceCollector[S, I, O], error) {
	if err := validateAssuranceAdmission(m, metadata, limits); err != nil {
		return nil, err
	}
	collector := &AssuranceCollector[S, I, O]{manifest: cloneAssuranceManifest(m), metadata: metadata, properties: map[AssuranceProperty]AssuranceCheckCounts{}, families: map[string]bool{}, goals: map[string]bool{}, declaredFamilies: make(map[string]bool, len(m.Families)), declaredGoals: make(map[string]bool, len(m.Goals)), errors: map[string]bool{}, coverage: coverage, limits: limits}
	for _, f := range collector.manifest.Families {
		collector.declaredFamilies[f.Name] = true
	}
	for _, g := range collector.manifest.Goals {
		collector.declaredGoals[g.ID] = true
		if g.Scope == "history" {
			collector.needsHistory = true
		}
	}
	return collector, nil
}

func validateAssuranceAdmission(m AssuranceManifest, metadata Metadata, limits AssuranceObservationLimits) error {
	if limits.MaxProperties <= 0 || limits.MaxErrors <= 0 || limits.MaxBatchChecks <= 0 || limits.MaxCoverageHits <= 0 || limits.MaxTextBytes <= 0 || limits.MaxFamilies <= 0 || limits.MaxGoals <= 0 || limits.MaxBounds <= 0 || limits.MaxManifestBytes <= 0 || len(m.Properties) > limits.MaxProperties || len(m.Families) > limits.MaxFamilies || len(m.Goals) > limits.MaxGoals || len(m.Bounds) > limits.MaxBounds {
		return &ConfigError{"invalid assurance observation limits"}
	}
	used := 0
	charge := func(n int) bool {
		if n < 0 || n > limits.MaxManifestBytes-used {
			return false
		}
		used += n
		return true
	}
	for _, text := range []string{m.Model, m.Provenance.Manifest, m.Provenance.Model, m.Provenance.Properties, metadata.Name, metadata.Build} {
		if len(text) > limits.MaxTextBytes || !charge(16) || !charge(len(text)) {
			return &ConfigError{"assurance identity exceeds observation limits"}
		}
	}
	for k, v := range m.Bounds {
		if len(k) > limits.MaxTextBytes || len(v) > limits.MaxTextBytes || !charge(64) || !charge(len(k)) || !charge(len(v)) {
			return &ConfigError{"assurance bound exceeds observation limits"}
		}
	}
	for _, p := range m.Properties {
		if len(p.ID) > limits.MaxTextBytes || !charge(64) || !charge(len(p.ID)) || !charge(len(p.Phase)) {
			return &ConfigError{"assurance property exceeds observation text limit"}
		}
	}
	for _, f := range m.Families {
		if len(f.Name) > limits.MaxTextBytes || len(f.Reason) > limits.MaxTextBytes || !charge(64) || !charge(len(f.Name)) || !charge(len(f.Reason)) {
			return &ConfigError{"assurance family exceeds observation text limit"}
		}
	}
	for _, g := range m.Goals {
		if len(g.ID) > limits.MaxTextBytes || !charge(64) || !charge(len(g.ID)) || !charge(len(g.Scope)) {
			return &ConfigError{"assurance goal exceeds observation text limit"}
		}
	}
	if err := m.Validate(); err != nil {
		return err
	}
	if err := metadata.Validate(); err != nil {
		return err
	}
	if m.Model != metadata.Name {
		return &ModelError{"assurance manifest model does not match actual model metadata"}
	}
	return nil
}

func (a *AssuranceCollector[S, I, O]) recordError(message string) {
	if len(message) > a.limits.MaxTextBytes || (!a.errors[message] && len(a.errors) >= a.limits.MaxErrors) {
		a.limitExceeded = true
		return
	}
	a.errors[message] = true
}
func assuranceInc(n *uint64) {
	if *n < math.MaxUint64 {
		*n++
	}
}
func (a *AssuranceCollector[S, I, O]) checks(phase string, checks []Check, complete bool) {
	if len(checks) > a.limits.MaxBatchChecks {
		a.limitExceeded = true
		return
	}
	emitted := map[string]bool{}
	for _, c := range checks {
		if len(c.ID) > a.limits.MaxTextBytes {
			a.limitExceeded = true
			continue
		}
		if emitted[c.ID] {
			a.recordError(fmt.Sprintf("ambiguous duplicate property ID in %s batch: %s", phase, c.ID))
		}
		emitted[c.ID] = true
		p := AssuranceProperty{c.ID, phase}
		if _, ok := a.properties[p]; !ok && len(a.properties) >= a.limits.MaxProperties {
			a.limitExceeded = true
			continue
		}
		counts := a.properties[p]
		switch c.Status {
		case "passed":
			assuranceInc(&counts.Passed)
		case "failed":
			assuranceInc(&counts.Failed)
		case "skipped":
			assuranceInc(&counts.Skipped)
		default:
			a.recordError("invalid check status")
		}
		a.properties[p] = counts
	}
	if complete {
		for _, p := range a.manifest.Properties {
			if p.Phase == phase && !emitted[p.ID] {
				if _, ok := a.properties[p]; !ok && len(a.properties) >= a.limits.MaxProperties {
					a.limitExceeded = true
					continue
				}
				v := a.properties[p]
				assuranceInc(&v.Missing)
				a.properties[p] = v
			}
		}
	}
}
func (a *AssuranceCollector[S, I, O]) Observer() SearchObserver[S, I, O] {
	return SearchObserver[S, I, O]{
		NeedsHistory: func() bool { return a.needsHistory },
		Initial: func(_ S, c []Check) error {
			if !a.enter() {
				return &ModelError{"assurance collector busy"}
			}
			defer a.busy.Store(false)
			a.checks("state", c, true)
			return nil
		},
		CheckError: func(phase string, c []Check, complete bool) error {
			if !a.enter() {
				return &ModelError{"assurance collector busy"}
			}
			defer a.busy.Store(false)
			if phase == "initial_state" {
				phase = "state"
			}
			a.checks(phase, c, complete)
			return nil
		},
		Transition: func(before S, input I, after Transition[S, O], state, transition []Check, history []I) error {
			if !a.enter() {
				return &ModelError{"assurance collector busy"}
			}
			defer a.busy.Store(false)
			a.checks("state", state, true)
			a.checks("transition", transition, true)
			assuranceInc(&a.transitions)
			var hits AssuranceHits
			var err error
			if a.coverage != nil {
				hits, err = call("assurance coverage", func() (AssuranceHits, error) { return a.coverage(before, input, after, history) })
			}
			if err != nil {
				a.recordError("coverage callback: " + err.Error())
				assuranceInc(&a.unclassified)
				return nil
			}
			if len(hits.Families) > a.limits.MaxCoverageHits || len(hits.Goals) > a.limits.MaxCoverageHits-len(hits.Families) {
				a.limitExceeded = true
				assuranceInc(&a.unclassified)
				return nil
			}
			for _, name := range hits.Families {
				if len(name) > a.limits.MaxTextBytes {
					a.limitExceeded = true
					assuranceInc(&a.unclassified)
					return nil
				}
			}
			for _, id := range hits.Goals {
				if len(id) > a.limits.MaxTextBytes {
					a.limitExceeded = true
					assuranceInc(&a.unclassified)
					return nil
				}
			}
			classified := false
			for _, name := range hits.Families {
				if a.declaredFamilies[name] {
					classified = true
					a.families[name] = true
				} else {
					a.recordError("undeclared input family: " + name)
				}
			}
			if !classified {
				assuranceInc(&a.unclassified)
			}
			for _, id := range hits.Goals {
				if a.declaredGoals[id] {
					a.goals[id] = true
				} else {
					a.recordError("undeclared cover goal: " + id)
				}
			}
			return nil
		},
	}
}
func (a *AssuranceCollector[S, I, O]) Finish(outcome, termination string, runErr error) AssuranceEvidence {
	if !a.enter() {
		return AssuranceEvidence{Errors: []string{"assurance collector busy"}}
	}
	defer a.busy.Store(false)
	if runErr != nil {
		a.recordError(runErr.Error())
	}
	e := AssuranceEvidence{Manifest: cloneAssuranceManifest(a.manifest), Metadata: a.metadata, Outcome: outcome, Termination: termination, CheckedTransitions: a.transitions, UnclassifiedTransitions: a.unclassified, Properties: map[AssuranceProperty]AssuranceCheckCounts{}, Families: map[string]bool{}, Goals: map[string]bool{}}
	// Budgets are supplied by the run (Rust Observer::finish takes them as an
	// argument); observation admission limits are not Rust budget keys.
	e.Budgets = map[string]string{}
	for k, v := range a.properties {
		e.Properties[k] = v
	}
	for k, v := range a.families {
		e.Families[k] = v
	}
	for k, v := range a.goals {
		e.Goals[k] = v
	}
	for k := range a.errors {
		e.Errors = append(e.Errors, k)
	}
	if a.limitExceeded {
		e.Errors = append(e.Errors, "assurance observation limit exceeded")
	}
	if a.rejected.Load() {
		e.Errors = append(e.Errors, "assurance collector concurrent or reentrant observation")
	}
	slices.Sort(e.Errors)
	e.Errors = slices.Compact(e.Errors)
	return e
}

// AssessStrict conservatively evaluates raw evidence and rejects inconsistent
// reports before making any adequacy claim. It cannot authenticate callbacks.
func (e AssuranceEvidence) AssessStrict() AssuranceAssessment {
	fail := func(reason string) AssuranceAssessment {
		return AssuranceAssessment{Reasons: []string{reason}, Confidence: "The report is internally inconsistent; no adequacy claim is established."}
	}
	if err := e.Manifest.Validate(); err != nil {
		return fail("inconsistent assurance report: " + err.Error())
	}
	if err := e.Metadata.Validate(); err != nil {
		return fail("inconsistent assurance report: invalid metadata")
	}
	for p := range e.Properties {
		if !utf8.ValidString(p.ID) || (p.Phase != "state" && p.Phase != "transition") {
			return fail("inconsistent assurance report: invalid observed property")
		}
	}
	for k, v := range e.Budgets {
		if !utf8.ValidString(k) || !utf8.ValidString(v) {
			return fail("inconsistent assurance report: invalid budget UTF-8")
		}
	}
	for _, v := range e.Errors {
		if !utf8.ValidString(v) {
			return fail("inconsistent assurance report: invalid error UTF-8")
		}
	}
	for k := range e.Families {
		if !utf8.ValidString(k) {
			return fail("inconsistent assurance report: invalid family UTF-8")
		}
	}
	for k := range e.Goals {
		if !utf8.ValidString(k) {
			return fail("inconsistent assurance report: invalid goal UTF-8")
		}
	}
	if e.Metadata.Name != e.Manifest.Model || strings.TrimSpace(e.Termination) == "" || e.UnclassifiedTransitions > e.CheckedTransitions {
		return fail("inconsistent assurance report: identity, termination, or transition count")
	}
	switch e.Outcome {
	case "graph_exhausted", "cases_completed", "depth_limited", "budget_limited", "interrupted", "timed_out", "failed", "not_started":
	default:
		return fail("inconsistent assurance report: unknown outcome")
	}
	validTermination := false
	switch e.Termination {
	case "graph_exhausted", "cases_completed", "depth_bound", "state_limit", "transition_limit", "estimated_state_byte_limit", "cancelled", "deadline", "failure_found":
		validTermination = assuranceOutcome(e.Termination) == e.Outcome
	case "ModelError", "Panicked":
		// Rust finish_fuzz records "ModelError"; Rust run_campaign records
		// "Panicked" for a job that unwound. Both always carry an error.
		validTermination = e.Outcome == "failed" && len(e.Errors) > 0
	default:
		// "MergedJobs" (merge_reports) or "Campaign::<Debug>" (run_campaign).
		// The proof seals the derived aggregate once at construction, so
		// validation is a comparison rather than a full re-merge.
		if e.merged != nil && e.merged.sealed != nil && (e.Termination == "MergedJobs" || strings.HasPrefix(e.Termination, "Campaign::")) {
			actual := e
			actual.merged = nil
			validTermination = reflect.DeepEqual(actual, *e.merged.sealed)
		}
	}
	if !validTermination {
		return fail("inconsistent assurance report: termination does not match outcome")
	}
	declaredFamilies := map[string]bool{}
	for _, f := range e.Manifest.Families {
		declaredFamilies[f.Name] = true
	}
	declaredGoals := map[string]bool{}
	for _, g := range e.Manifest.Goals {
		declaredGoals[g.ID] = true
	}
	for name, hit := range e.Families {
		if hit && !declaredFamilies[name] {
			return fail("inconsistent assurance report: undeclared input family")
		}
	}
	for id, hit := range e.Goals {
		if hit && !declaredGoals[id] {
			return fail("inconsistent assurance report: undeclared cover goal")
		}
	}
	if e.CheckedTransitions == 0 {
		for _, hit := range e.Families {
			if hit {
				return fail("inconsistent assurance report: coverage without checked transitions")
			}
		}
		for _, hit := range e.Goals {
			if hit {
				return fail("inconsistent assurance report: coverage without checked transitions")
			}
		}
	}
	reasons := []string{}
	if len(e.Manifest.Properties) == 0 {
		reasons = append(reasons, "no expected properties declared")
	}
	required := false
	for _, f := range e.Manifest.Families {
		if !f.Excluded {
			required = true
		}
	}
	if !required {
		reasons = append(reasons, "no required input families declared")
	}
	properties := slices.Clone(e.Manifest.Properties)
	slices.SortFunc(properties, func(a, b AssuranceProperty) int {
		if n := strings.Compare(a.ID, b.ID); n != 0 {
			return n
		}
		return strings.Compare(a.Phase, b.Phase)
	})
	for _, p := range properties {
		c := e.Properties[p]
		status := "Passed"
		switch {
		case c.Failed > 0:
			status = "Failed"
		case c.Skipped > 0:
			status = "Skipped"
		case c.Missing > 0 || c.Passed == 0:
			status = "Missing"
		}
		if status != "Passed" {
			phase := "State"
			if p.Phase == "transition" {
				phase = "Transition"
			}
			reasons = append(reasons, fmt.Sprintf("property %s %s: %s", p.ID, phase, status))
		}
	}
	for _, c := range e.Properties {
		if c.Failed > 0 || c.Skipped > 0 || c.Missing > 0 {
			reasons = append(reasons, "observed failed, skipped, or omitted required checks")
			break
		}
	}
	families := slices.Clone(e.Manifest.Families)
	slices.SortFunc(families, func(a, b AssuranceFamily) int { return strings.Compare(a.Name, b.Name) })
	for _, f := range families {
		if !f.Excluded && !e.Families[f.Name] {
			reasons = append(reasons, "input family "+f.Name+": Missing")
		}
		if f.Excluded && e.Families[f.Name] {
			// Rust formats FamilyStatus with {:?}, so the reason uses Rust
			// str Debug escaping rather than Go quoting.
			reasons = append(reasons, "input family "+f.Name+": ExclusionViolated { reason: "+rustDebugString(f.Reason)+" }")
		}
	}
	goals := slices.Clone(e.Manifest.Goals)
	slices.SortFunc(goals, func(a, b AssuranceGoal) int { return strings.Compare(a.ID, b.ID) })
	for _, g := range goals {
		if !e.Goals[g.ID] {
			status := "NotReachedBeforeStop"
			if e.Outcome == "graph_exhausted" && len(e.Errors) == 0 && g.Scope == "transition" {
				status = "UnreachableUnderDeclaredModel"
			}
			reasons = append(reasons, "cover goal "+g.ID+": "+status)
		}
	}
	if e.UnclassifiedTransitions > 0 {
		reasons = append(reasons, "some checked inputs had no declared family classification")
	}
	if len(e.Errors) > 0 {
		reasons = append(reasons, "model or observation callback error")
	}
	if (e.Manifest.RequiredOutcome == "graph_exhausted" && e.Outcome != "graph_exhausted") || (e.Manifest.RequiredOutcome == "completed" && e.Outcome != "graph_exhausted" && e.Outcome != "cases_completed") {
		reasons = append(reasons, "required search outcome not met: "+assuranceVariant(e.Outcome))
	}
	assessment := AssuranceAssessment{Adequate: len(reasons) == 0, Reasons: reasons, Confidence: "Declared assurance requirements are not fully satisfied; no broader adequacy claim is established."}
	if assessment.Adequate {
		if e.Outcome == "graph_exhausted" {
			assessment.Confidence = "The declared properties held for the exhausted finite model and declared adequacy goals were observed."
		} else {
			assessment.Confidence = "The declared properties held for the completed sampled cases and declared adequacy goals were observed."
		}
	}
	return assessment
}

// AssuredRun retains both the search result (or error) and all observations
// gathered before the search stopped. Outer errors indicate setup failed.
type AssuredRun[R any] struct {
	Search      *R
	SearchError error
	Assurance   AssuranceEvidence
}

func assuranceMetadata[S, I, O any](model Model[S, I, O]) (Metadata, error) {
	if model.Codec == nil || model.Codec.Metadata == nil {
		return Metadata{}, &ModelError{"assurance requires actual model metadata"}
	}
	metadata, err := call("assurance model metadata", model.Codec.Metadata)
	if err != nil {
		return Metadata{}, err
	}
	if err = metadata.Validate(); err != nil {
		return Metadata{}, err
	}
	return metadata, nil
}
func assuranceOutcome(termination string) string {
	switch termination {
	case "graph_exhausted", "cases_completed":
		return termination
	case "depth_bound":
		return "depth_limited"
	case "state_limit", "transition_limit", "estimated_state_byte_limit":
		return "budget_limited"
	case "cancelled":
		return "interrupted"
	case "deadline":
		return "timed_out"
	case "failure_found":
		return "failed"
	default:
		return "failed"
	}
}
func assuranceControlBudgets(limits RunLimits, b map[string]string) {
	b["cancellation_enabled"] = strconv.FormatBool(limits.Cancellation != nil)
	if limits.MaxDuration != nil {
		b["max_duration_ns"] = strconv.FormatInt(int64(*limits.MaxDuration), 10)
	}
	if limits.MaxDurationNS != nil {
		high := new(big.Int).SetUint64(limits.MaxDurationNS.Hi)
		high.Lsh(high, 64).Or(high, new(big.Int).SetUint64(limits.MaxDurationNS.Lo))
		b["max_duration_ns"] = high.String()
	}
}
func AssuranceEnumerate[S, I, O any](m Model[S, I, O], manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config SearchConfig, limits SearchLimits) (AssuredRun[SearchReport[I]], error) {
	return AssuranceEnumerateWithObservationLimits(m, manifest, coverage, config, limits, DefaultAssuranceObservationLimits())
}

func AssuranceEnumerateWithObservationLimits[S, I, O any](m Model[S, I, O], manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config SearchConfig, limits SearchLimits, observationLimits AssuranceObservationLimits) (AssuredRun[SearchReport[I]], error) {
	limits.Control = snapshotRunLimits(limits.Control)
	var result AssuredRun[SearchReport[I]]
	if err := validateAssuranceAdmission(manifest, Metadata{Name: manifest.Model}, observationLimits); err != nil {
		return result, err
	}
	metadata, err := assuranceMetadata(m)
	if err != nil {
		return result, err
	}
	collector, err := NewAssuranceCollectorWithLimits(manifest, metadata, coverage, observationLimits)
	if err != nil {
		return result, err
	}
	budgets := map[string]string{"max_states": strconv.Itoa(config.MaxStates), "max_transitions": strconv.FormatUint(config.MaxTransitions, 10), "max_depth": strconv.Itoa(config.MaxDepth)}
	assuranceControlBudgets(limits.Control, budgets)
	if limits.MaxEstimatedStateBytes != nil {
		v := *limits.MaxEstimatedStateBytes
		limits.MaxEstimatedStateBytes = &v
		budgets["max_estimated_state_bytes"] = strconv.FormatUint(v, 10)
	}
	if limits.Control.MaxDuration != nil {
		v := *limits.Control.MaxDuration
		limits.Control.MaxDuration = &v
	}
	search, searchErr := EnumerateObserved(m, config, limits, collector.Observer())
	outcome, termination := "failed", "ModelError"
	if searchErr == nil && search != nil {
		termination = search.Termination
		outcome = assuranceOutcome(termination)
	}
	evidence := collector.Finish(outcome, termination, searchErr)
	evidence.Budgets = budgets
	return AssuredRun[SearchReport[I]]{Search: search, SearchError: searchErr, Assurance: evidence}, nil
}
func AssuranceFuzz64[S, I, O any](g GenerateModel[S, I, O], manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, limits RunLimits) (AssuredRun[FuzzReport64[I]], error) {
	return AssuranceFuzz64WithObservationLimits(g, manifest, coverage, config, limits, DefaultAssuranceObservationLimits())
}

func AssuranceFuzz64WithObservationLimits[S, I, O any](g GenerateModel[S, I, O], manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, limits RunLimits, observationLimits AssuranceObservationLimits) (AssuredRun[FuzzReport64[I]], error) {
	limits = snapshotRunLimits(limits)
	var result AssuredRun[FuzzReport64[I]]
	if err := validateAssuranceAdmission(manifest, Metadata{Name: manifest.Model}, observationLimits); err != nil {
		return result, err
	}
	metadata, err := assuranceMetadata(g.Model)
	if err != nil {
		return result, err
	}
	collector, err := NewAssuranceCollectorWithLimits(manifest, metadata, coverage, observationLimits)
	if err != nil {
		return result, err
	}
	budgets := map[string]string{"seed": strconv.FormatUint(config.Seed, 10), "cases": strconv.FormatUint(config.Cases, 10), "max_steps": strconv.FormatUint(config.MaxSteps, 10), "max_transitions": strconv.FormatUint(config.MaxTransitions, 10), "mutation_percent": strconv.Itoa(config.MutationPercent)}
	assuranceControlBudgets(limits, budgets)
	if limits.MaxDuration != nil {
		v := *limits.MaxDuration
		limits.MaxDuration = &v
	}
	search, searchErr := FuzzObserved64(g, config, limits, collector.Observer())
	outcome, termination := "failed", "ModelError"
	if searchErr == nil && search != nil {
		termination = search.Termination
		outcome = assuranceOutcome(termination)
	}
	evidence := collector.Finish(outcome, termination, searchErr)
	evidence.Budgets = budgets
	return AssuredRun[FuzzReport64[I]]{Search: search, SearchError: searchErr, Assurance: evidence}, nil
}

// AssuranceGuidedFuzz64 records the feedback identity used by the actual
// guided run, alongside the independent assurance observations.
func AssuranceGuidedFuzz64[S, I, O, K any](g GenerateModel[S, I, O], manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits) (AssuredRun[FuzzReport64[I]], error) {
	return AssuranceGuidedFuzz64WithObservationLimits(g, manifest, coverage, config, guidance, feedback, limits, DefaultAssuranceObservationLimits())
}

func AssuranceGuidedFuzz64WithObservationLimits[S, I, O, K any](g GenerateModel[S, I, O], manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits, observationLimits AssuranceObservationLimits) (AssuredRun[FuzzReport64[I]], error) {
	limits = snapshotRunLimits(limits)
	var result AssuredRun[FuzzReport64[I]]
	if err := validateAssuranceAdmission(manifest, Metadata{Name: manifest.Model}, observationLimits); err != nil {
		return result, err
	}
	metadata, err := assuranceMetadata(g.Model)
	if err != nil {
		return result, err
	}
	collector, err := NewAssuranceCollectorWithLimits(manifest, metadata, coverage, observationLimits)
	if err != nil {
		return result, err
	}
	var identity FeedbackMetadata
	var identityKnown bool
	if original := feedback.Metadata; original != nil {
		feedback.Metadata = func() (FeedbackMetadata, error) {
			value, err := original()
			if err == nil {
				identity, identityKnown = value, true
			}
			return value, err
		}
	}
	budgets := map[string]string{"seed": strconv.FormatUint(config.Seed, 10), "cases": strconv.FormatUint(config.Cases, 10), "max_steps": strconv.FormatUint(config.MaxSteps, 10), "max_transitions": strconv.FormatUint(config.MaxTransitions, 10), "mutation_percent": strconv.Itoa(config.MutationPercent), "guidance.algorithm": GuidanceAlgorithm, "guidance.max_corpus_entries": strconv.FormatUint(guidance.MaxCorpusEntries, 10), "guidance.max_corpus_inputs": strconv.FormatUint(guidance.MaxCorpusInputs, 10), "guidance.max_features": strconv.FormatUint(guidance.MaxFeatures, 10), "guidance.max_features_per_observation": strconv.FormatUint(guidance.MaxFeaturesPerObservation, 10)}
	assuranceControlBudgets(limits, budgets)
	if limits.MaxDuration != nil {
		v := *limits.MaxDuration
		limits.MaxDuration = &v
	}
	search, searchErr := GuidedFuzzObserved64(g, config, guidance, feedback, limits, collector.Observer())
	if identityKnown {
		budgets["guidance.feedback.name"] = identity.Name
		budgets["guidance.feedback.version"] = strconv.FormatUint(uint64(identity.Version), 10)
		budgets["guidance.feedback.build"] = identity.Build
	}
	outcome, termination := "failed", "ModelError"
	if searchErr == nil && search != nil {
		termination = search.Termination
		outcome = assuranceOutcome(termination)
	}
	evidence := collector.Finish(outcome, termination, searchErr)
	evidence.Budgets = budgets
	return AssuredRun[FuzzReport64[I]]{Search: search, SearchError: searchErr, Assurance: evidence}, nil
}

// AssuranceFuzzAuto64 combines assurance observations with cooperative automatic scans.
func AssuranceFuzzAuto64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, limits RunLimits) (AssuredRun[FuzzReport64[I]], error) {
	return AssuranceFuzzAuto64WithObservationLimits(m, equal, options, manifest, coverage, config, limits, DefaultAssuranceObservationLimits())
}

func AssuranceFuzzAuto64WithObservationLimits[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, limits RunLimits, observationLimits AssuranceObservationLimits) (AssuredRun[FuzzReport64[I]], error) {
	limits = snapshotRunLimits(limits)
	var result AssuredRun[FuzzReport64[I]]
	if err := validateAssuranceAdmission(manifest, Metadata{Name: manifest.Model}, observationLimits); err != nil {
		return result, err
	}
	metadata, err := assuranceMetadata(m)
	if err != nil {
		return result, err
	}
	collector, err := NewAssuranceCollectorWithLimits(manifest, metadata, coverage, observationLimits)
	if err != nil {
		return result, err
	}
	budgets := map[string]string{"seed": strconv.FormatUint(config.Seed, 10), "cases": strconv.FormatUint(config.Cases, 10), "max_steps": strconv.FormatUint(config.MaxSteps, 10), "max_transitions": strconv.FormatUint(config.MaxTransitions, 10), "mutation_percent": strconv.Itoa(config.MutationPercent)}
	assuranceControlBudgets(limits, budgets)
	if limits.MaxDuration != nil {
		v := *limits.MaxDuration
		limits.MaxDuration = &v
	}
	search, searchErr := FuzzAutoObserved64(m, equal, options, config, limits, collector.Observer())
	outcome, termination := "failed", "ModelError"
	if searchErr == nil && search != nil {
		termination = search.Termination
		outcome = assuranceOutcome(termination)
	}
	evidence := collector.Finish(outcome, termination, searchErr)
	evidence.Budgets = budgets
	return AssuredRun[FuzzReport64[I]]{Search: search, SearchError: searchErr, Assurance: evidence}, nil
}

// AssuranceGuidedFuzzAuto64 combines guided assurance with cooperative automatic scans.
func AssuranceGuidedFuzzAuto64[S, I, O, K any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits) (AssuredRun[FuzzReport64[I]], error) {
	return AssuranceGuidedFuzzAuto64WithObservationLimits(m, equal, options, manifest, coverage, config, guidance, feedback, limits, DefaultAssuranceObservationLimits())
}

func AssuranceGuidedFuzzAuto64WithObservationLimits[S, I, O, K any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, manifest AssuranceManifest, coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error), config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits, observationLimits AssuranceObservationLimits) (AssuredRun[FuzzReport64[I]], error) {
	limits = snapshotRunLimits(limits)
	var result AssuredRun[FuzzReport64[I]]
	if err := validateAssuranceAdmission(manifest, Metadata{Name: manifest.Model}, observationLimits); err != nil {
		return result, err
	}
	metadata, err := assuranceMetadata(m)
	if err != nil {
		return result, err
	}
	collector, err := NewAssuranceCollectorWithLimits(manifest, metadata, coverage, observationLimits)
	if err != nil {
		return result, err
	}
	var identity FeedbackMetadata
	var identityKnown bool
	if original := feedback.Metadata; original != nil {
		feedback.Metadata = func() (FeedbackMetadata, error) {
			value, err := original()
			if err == nil {
				identity, identityKnown = value, true
			}
			return value, err
		}
	}
	budgets := map[string]string{"seed": strconv.FormatUint(config.Seed, 10), "cases": strconv.FormatUint(config.Cases, 10), "max_steps": strconv.FormatUint(config.MaxSteps, 10), "max_transitions": strconv.FormatUint(config.MaxTransitions, 10), "mutation_percent": strconv.Itoa(config.MutationPercent), "guidance.algorithm": GuidanceAlgorithm, "guidance.max_corpus_entries": strconv.FormatUint(guidance.MaxCorpusEntries, 10), "guidance.max_corpus_inputs": strconv.FormatUint(guidance.MaxCorpusInputs, 10), "guidance.max_features": strconv.FormatUint(guidance.MaxFeatures, 10), "guidance.max_features_per_observation": strconv.FormatUint(guidance.MaxFeaturesPerObservation, 10)}
	assuranceControlBudgets(limits, budgets)
	if limits.MaxDuration != nil {
		v := *limits.MaxDuration
		limits.MaxDuration = &v
	}
	search, searchErr := GuidedFuzzAutoObserved64(m, equal, options, config, guidance, feedback, limits, collector.Observer())
	if identityKnown {
		budgets["guidance.feedback.name"] = identity.Name
		budgets["guidance.feedback.version"] = strconv.FormatUint(uint64(identity.Version), 10)
		budgets["guidance.feedback.build"] = identity.Build
	}
	outcome, termination := "failed", "ModelError"
	if searchErr == nil && search != nil {
		termination = search.Termination
		outcome = assuranceOutcome(termination)
	}
	evidence := collector.Finish(outcome, termination, searchErr)
	evidence.Budgets = budgets
	return AssuredRun[FuzzReport64[I]]{Search: search, SearchError: searchErr, Assurance: evidence}, nil
}

// AssuranceJob associates an individual evidence snapshot with its campaign ID.
type AssuranceJob struct {
	ID       uint64
	Evidence AssuranceEvidence
}
type MergedAssurance struct {
	Report      AssuranceEvidence
	Jobs        []AssuranceJob
	MissingJobs []uint64
}
type assuranceMergeProof struct {
	expected        []uint64
	jobs            []AssuranceJob
	limits          AssuranceObservationLimits
	mergeLimits     AssuranceMergeLimits
	campaign        *CampaignReport64
	campaignBudgets map[string]string
	// maxEvidenceBytes is the Go-only campaign ledger bound. It is kept out of
	// the Rust-defined search budgets and only appears in the Go "campaign"
	// JSON extension.
	maxEvidenceBytes uint64
	// fromReport marks campaign facts reconstructed from a genuine Rust
	// report (which has no coordinator extension); JSON then omits it.
	fromReport bool
	// sealed is the derived aggregate, recorded once whenever the proof is
	// built or a campaign is applied. It is immutable and may be shared.
	sealed *AssuranceEvidence
}

// sealAssuranceMerge records the derived aggregate so later validation does
// not re-merge every job. Must be called after any change to the aggregate.
func sealAssuranceMerge(e *AssuranceEvidence) {
	sealed := cloneAssuranceEvidence(*e)
	sealed.merged = nil
	e.merged.sealed = &sealed
}

func cloneAssuranceEvidence(e AssuranceEvidence) AssuranceEvidence {
	e.Manifest = cloneAssuranceManifest(e.Manifest)
	e.Properties = maps.Clone(e.Properties)
	e.Families = maps.Clone(e.Families)
	e.Goals = maps.Clone(e.Goals)
	e.Budgets = maps.Clone(e.Budgets)
	e.Errors = slices.Clone(e.Errors)
	if e.merged != nil {
		p := &assuranceMergeProof{expected: slices.Clone(e.merged.expected), limits: e.merged.limits, mergeLimits: e.merged.mergeLimits, maxEvidenceBytes: e.merged.maxEvidenceBytes, fromReport: e.merged.fromReport, sealed: e.merged.sealed}
		p.campaignBudgets = maps.Clone(e.merged.campaignBudgets)
		if e.merged.campaign != nil {
			v := *e.merged.campaign
			p.campaign = &v
		}
		for _, j := range e.merged.jobs {
			p.jobs = append(p.jobs, AssuranceJob{j.ID, cloneAssuranceEvidence(j.Evidence)})
		}
		e.merged = p
	}
	return e
}
func assuranceAdd(a, b uint64) uint64 {
	if b > math.MaxUint64-a {
		return math.MaxUint64
	}
	return a + b
}

// MergeAssuranceReports preserves each validated job, including failed and
// partial jobs. Missing jobs cannot be substituted by successful siblings.
func MergeAssuranceReports(manifest AssuranceManifest, metadata Metadata, expected []uint64, reports []AssuranceJob) (MergedAssurance, error) {
	return MergeAssuranceReportsWithLimits(manifest, metadata, expected, reports, DefaultAssuranceObservationLimits())
}

// MergeAssuranceReportsWithLimits allows explicit catalog admission limits.
func MergeAssuranceReportsWithLimits(manifest AssuranceManifest, metadata Metadata, expected []uint64, reports []AssuranceJob, limits AssuranceObservationLimits) (MergedAssurance, error) {
	return MergeAssuranceReportsWithBounds(manifest, metadata, expected, reports, limits, DefaultAssuranceMergeLimits())
}

// AssuranceMergeLimits bound logical job evidence, including nested proofs;
// they do not bound memory the caller has already allocated.
type AssuranceMergeLimits struct{ MaxJobs, MaxEvidenceBytes, MaxNestedDepth uint64 }

func DefaultAssuranceMergeLimits() AssuranceMergeLimits {
	return AssuranceMergeLimits{MaxJobs: 100000, MaxEvidenceBytes: 64 << 20, MaxNestedDepth: 32}
}

// MergeAssuranceReportsWithBounds admits job evidence before cloning it.
func MergeAssuranceReportsWithBounds(manifest AssuranceManifest, metadata Metadata, expected []uint64, reports []AssuranceJob, limits AssuranceObservationLimits, mergeLimits AssuranceMergeLimits) (MergedAssurance, error) {
	var result MergedAssurance
	if err := validateAssuranceMergeAdmission(expected, reports, mergeLimits); err != nil {
		return result, err
	}
	if err := validateAssuranceAdmission(manifest, metadata, limits); err != nil {
		return result, err
	}
	if metadata.Name != manifest.Model {
		return result, &ModelError{"merge model identity mismatch"}
	}
	manifest = cloneAssuranceManifest(manifest)
	wanted := map[uint64]bool{}
	for _, id := range expected {
		if wanted[id] {
			return result, &ModelError{"duplicate expected assurance job"}
		}
		wanted[id] = true
	}
	jobs := map[uint64]AssuranceEvidence{}
	for _, j := range reports {
		if !wanted[j.ID] {
			return result, &ModelError{"unexpected assurance job"}
		}
		if _, ok := jobs[j.ID]; ok {
			return result, &ModelError{"duplicate assurance job report"}
		}
		a := j.Evidence.AssessStrict()
		if len(a.Reasons) > 0 && strings.HasPrefix(a.Reasons[0], "inconsistent assurance report:") {
			return result, &ModelError{a.Reasons[0]}
		}
		if !reflect.DeepEqual(cloneAssuranceManifest(j.Evidence.Manifest), manifest) || j.Evidence.Metadata != metadata {
			return result, &ModelError{"cannot merge different assurance manifests or executable identities"}
		}
		jobs[j.ID] = cloneAssuranceEvidence(j.Evidence)
	}
	ids := slices.Clone(expected)
	slices.Sort(ids)
	for _, id := range ids {
		if j, ok := jobs[id]; ok {
			result.Jobs = append(result.Jobs, AssuranceJob{id, j})
		} else {
			result.MissingJobs = append(result.MissingJobs, id)
		}
	}
	aggregate := AssuranceEvidence{Manifest: manifest, Metadata: metadata, Termination: "MergedJobs", Properties: map[AssuranceProperty]AssuranceCheckCounts{}, Families: map[string]bool{}, Goals: map[string]bool{}, Budgets: map[string]string{"requested_jobs": strconv.Itoa(len(ids)), "reported_jobs": strconv.Itoa(len(result.Jobs))}}
	errorsSet := map[string]bool{}
	allGraph, allComplete := true, true
	failed, timedOut, interrupted, budget := false, false, false, false
	for _, job := range result.Jobs {
		e := job.Evidence
		for p, c := range e.Properties {
			v := aggregate.Properties[p]
			v.Passed = assuranceAdd(v.Passed, c.Passed)
			v.Failed = assuranceAdd(v.Failed, c.Failed)
			v.Skipped = assuranceAdd(v.Skipped, c.Skipped)
			v.Missing = assuranceAdd(v.Missing, c.Missing)
			aggregate.Properties[p] = v
		}
		for name, hit := range e.Families {
			if hit {
				aggregate.Families[name] = true
			}
		}
		for id, hit := range e.Goals {
			if hit {
				aggregate.Goals[id] = true
			}
		}
		for _, err := range e.Errors {
			errorsSet[fmt.Sprintf("job %d: %s", job.ID, err)] = true
		}
		aggregate.CheckedTransitions = assuranceAdd(aggregate.CheckedTransitions, e.CheckedTransitions)
		aggregate.UnclassifiedTransitions = assuranceAdd(aggregate.UnclassifiedTransitions, e.UnclassifiedTransitions)
		allGraph = allGraph && e.Outcome == "graph_exhausted"
		allComplete = allComplete && (e.Outcome == "graph_exhausted" || e.Outcome == "cases_completed")
		failed = failed || e.Outcome == "failed"
		timedOut = timedOut || e.Outcome == "timed_out"
		interrupted = interrupted || e.Outcome == "interrupted" || e.Outcome == "not_started"
		budget = budget || e.Outcome == "budget_limited"
	}
	for err := range errorsSet {
		aggregate.Errors = append(aggregate.Errors, err)
	}
	slices.Sort(aggregate.Errors)
	switch {
	case len(ids) == 0 || len(result.Jobs) == 0:
		aggregate.Outcome = "not_started"
	case failed:
		aggregate.Outcome = "failed"
	case len(result.MissingJobs) > 0:
		aggregate.Outcome = "interrupted"
	case allGraph:
		aggregate.Outcome = "graph_exhausted"
	case allComplete:
		aggregate.Outcome = "cases_completed"
	case timedOut:
		aggregate.Outcome = "timed_out"
	case interrupted:
		aggregate.Outcome = "interrupted"
	case budget:
		aggregate.Outcome = "budget_limited"
	default:
		aggregate.Outcome = "depth_limited"
	}
	proof := &assuranceMergeProof{expected: slices.Clone(ids), limits: limits, mergeLimits: mergeLimits}
	for _, j := range result.Jobs {
		proof.jobs = append(proof.jobs, AssuranceJob{j.ID, cloneAssuranceEvidence(j.Evidence)})
	}
	aggregate.merged = proof
	sealAssuranceMerge(&aggregate)
	result.Report = aggregate
	// Returned job copies do not alias the aggregate proof.
	for i := range result.Jobs {
		result.Jobs[i].Evidence = cloneAssuranceEvidence(result.Jobs[i].Evidence)
	}
	return result, nil
}

// AssuredCampaign keeps the coordinator report and the independent job ledger.
type AssuredCampaign struct {
	Campaign  CampaignReport64
	Assurance MergedAssurance
}

// DefaultAssuranceMaxJobs bounds the eagerly retained assurance job ledger.
const DefaultAssuranceMaxJobs uint64 = 100000
const DefaultAssuranceMaxEvidenceBytes uint64 = 64 << 20

// assuranceLedger bounds logical retained evidence, not callback allocations or
// peak copies made by merging. Each worker can stage one report before admission.
type assuranceLedger struct {
	mu            sync.Mutex
	used, maximum uint64
	reports       []AssuranceJob
}

func (l *assuranceLedger) retain(id uint64, e AssuranceEvidence) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	remaining := l.maximum - l.used
	charge := func(n uint64) bool {
		if n > remaining {
			return false
		}
		remaining -= n
		return true
	}
	text := func(v string) bool { return charge(uint64(len(v))) }
	if !charge(1024) {
		return false
	}
	for _, v := range []string{e.Manifest.Model, e.Manifest.Provenance.Manifest, e.Manifest.Provenance.Model, e.Manifest.Provenance.Properties, e.Metadata.Name, e.Metadata.Build, e.Outcome, e.Termination} {
		if !text(v) {
			return false
		}
	}
	for _, p := range e.Manifest.Properties {
		if !charge(64) || !text(p.ID) || !text(p.Phase) {
			return false
		}
	}
	for _, f := range e.Manifest.Families {
		if !charge(64) || !text(f.Name) || !text(f.Reason) {
			return false
		}
	}
	for _, g := range e.Manifest.Goals {
		if !charge(64) || !text(g.ID) || !text(g.Scope) {
			return false
		}
	}
	for k, v := range e.Manifest.Bounds {
		if !charge(64) || !text(k) || !text(v) {
			return false
		}
	}
	for p := range e.Properties {
		if !charge(128) || !text(p.ID) || !text(p.Phase) {
			return false
		}
	}
	for k := range e.Families {
		if !charge(64) || !text(k) {
			return false
		}
	}
	for k := range e.Goals {
		if !charge(64) || !text(k) {
			return false
		}
	}
	for _, v := range e.Errors {
		if !charge(32) || !text(v) {
			return false
		}
	}
	for k, v := range e.Budgets {
		if !charge(64) || !text(k) || !text(v) {
			return false
		}
	}
	l.used = l.maximum - remaining
	l.reports = append(l.reports, AssuranceJob{id, e})
	return true
}
func (l *assuranceLedger) snapshot() []AssuranceJob {
	l.mu.Lock()
	defer l.mu.Unlock()
	return slices.Clone(l.reports)
}
func (l *assuranceLedger) chargeExtra(n uint64) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if n > l.maximum-l.used {
		return false
	}
	l.used += n
	return true
}

func validateAssuranceMergeAdmission(expected []uint64, reports []AssuranceJob, limits AssuranceMergeLimits) error {
	if limits.MaxJobs == 0 || limits.MaxEvidenceBytes == 0 || limits.MaxNestedDepth == 0 {
		return &ConfigError{"invalid assurance merge limits"}
	}
	if uint64(len(expected)) > limits.MaxJobs || uint64(len(reports)) > limits.MaxJobs {
		return &ConfigError{"assurance merge job limit exceeded"}
	}
	type item struct {
		evidence AssuranceEvidence
		depth    uint64
	}
	stack := make([]item, 0, len(reports))
	for _, job := range reports {
		stack = append(stack, item{job.Evidence, 1})
	}
	ledger := &assuranceLedger{maximum: limits.MaxEvidenceBytes}
	var count uint64
	for len(stack) > 0 {
		last := len(stack) - 1
		current := stack[last]
		stack = stack[:last]
		if current.depth > limits.MaxNestedDepth || count >= limits.MaxJobs {
			return &ConfigError{"assurance merge nested proof limit exceeded"}
		}
		count++
		if !ledger.retain(count, current.evidence) {
			return &ConfigError{"assurance merge evidence byte limit exceeded"}
		}
		if proof := current.evidence.merged; proof != nil {
			if uint64(len(proof.expected)) > limits.MaxJobs || uint64(len(proof.jobs)) > limits.MaxJobs-count || uint64(len(stack)) > limits.MaxJobs-count-uint64(len(proof.jobs)) {
				return &ConfigError{"assurance merge nested proof limit exceeded"}
			}
			if uint64(len(proof.expected)) > math.MaxUint64/8 || !ledger.chargeExtra(uint64(len(proof.expected))*8) {
				return &ConfigError{"assurance merge evidence byte limit exceeded"}
			}
			for k, v := range proof.campaignBudgets {
				if !ledger.chargeExtra(64) || !ledger.chargeExtra(uint64(len(k))) || !ledger.chargeExtra(uint64(len(v))) {
					return &ConfigError{"assurance merge evidence byte limit exceeded"}
				}
			}
			for _, job := range proof.jobs {
				stack = append(stack, item{job.Evidence, current.depth + 1})
			}
		}
	}
	return nil
}
func assuranceEvidenceLimit(config CampaignConfig64) uint64 {
	if config.AssuranceMaxEvidenceBytes == 0 {
		return DefaultAssuranceMaxEvidenceBytes
	}
	return config.AssuranceMaxEvidenceBytes
}

func assuranceCampaignJobLimit(config CampaignConfig64) error {
	maximum := config.AssuranceMaxJobs
	if maximum == 0 {
		maximum = DefaultAssuranceMaxJobs
	}
	if config.Jobs > maximum || config.Jobs > uint64(int(^uint(0)>>1)) {
		return &ConfigError{"assurance campaign job ledger limit exceeded"}
	}
	return nil
}

func applyAssuranceCampaign(e *AssuranceEvidence, c CampaignReport64, budgets map[string]string) {
	for k, v := range budgets {
		e.Budgets[k] = v
	}
	e.Budgets["campaign.started"] = strconv.FormatUint(c.Started, 10)
	e.Budgets["campaign.finished"] = strconv.FormatUint(c.Finished, 10)
	e.Budgets["campaign.delivered"] = strconv.FormatUint(c.Delivered, 10)
	e.Budgets["campaign.accounting_complete"] = strconv.FormatBool(c.AccountingComplete)
	e.Termination = "Campaign::" + rustCampaignTermination(c.Termination)
	switch c.Termination.Kind {
	case "failure_found":
		e.Outcome = "failed"
	case "callback_error", "worker_error":
		e.Outcome = "failed"
		msg := "campaign completion/delivery error"
		if c.Termination.Error != nil {
			msg += ": " + c.Termination.Error.Error()
		}
		e.Errors = append(e.Errors, msg)
	case "cancelled":
		if e.Outcome != "failed" {
			e.Outcome = "interrupted"
		}
	case "deadline":
		if e.Outcome != "failed" {
			e.Outcome = "timed_out"
		}
	case "jobs_completed":
	default:
		e.Outcome = "failed"
		e.Errors = append(e.Errors, "unknown campaign termination")
	}
	if !c.AccountingComplete {
		e.Errors = append(e.Errors, "campaign accounting is incomplete")
	}
	if c.Delivered != c.Requested {
		e.Errors = append(e.Errors, "not every requested campaign report was delivered successfully")
	}
	slices.Sort(e.Errors)
	e.Errors = slices.Compact(e.Errors)
}

// AssuranceRunCampaign64 uses the existing coordinator and sink. Each job is
// independently identified and observed on its worker; coordinator completion,
// delivery and failure evidence remains part of the aggregate assessment.
func AssuranceRunCampaign64[S, I, O any](config CampaignConfig64, limits RunLimits, manifest AssuranceManifest, metadata Metadata,
	factory func(uint64) (CampaignModel[S, I, O], error),
	coverageFactory func(uint64, GenerateModel[S, I, O]) (func(S, I, Transition[S, O], []I) (AssuranceHits, error), error),
	sink func(JobReport64[I]) error,
) (AssuredCampaign, error) {
	return AssuranceRunCampaign64WithObservationLimits(config, limits, manifest, metadata, factory, coverageFactory, sink, DefaultAssuranceObservationLimits())
}

func AssuranceRunCampaign64WithObservationLimits[S, I, O any](config CampaignConfig64, limits RunLimits, manifest AssuranceManifest, metadata Metadata,
	factory func(uint64) (CampaignModel[S, I, O], error),
	coverageFactory func(uint64, GenerateModel[S, I, O]) (func(S, I, Transition[S, O], []I) (AssuranceHits, error), error),
	sink func(JobReport64[I]) error,
	observationLimits AssuranceObservationLimits,
) (AssuredCampaign, error) {
	var result AssuredCampaign
	limits = snapshotRunLimits(limits)
	if err := config.Validate(); err != nil {
		return result, err
	}
	if err := validateAssuranceAdmission(manifest, metadata, observationLimits); err != nil {
		return result, err
	}
	if manifest.Model != metadata.Name {
		return result, &ModelError{"campaign model identity mismatch"}
	}
	if err := assuranceCampaignJobLimit(config); err != nil {
		return result, err
	}
	manifest = cloneAssuranceManifest(manifest)
	budgets := map[string]string{"campaign.master_seed": strconv.FormatUint(config.MasterSeed, 10), "campaign.first_job": strconv.FormatUint(config.FirstJob, 10), "campaign.workers": strconv.FormatUint(config.Workers, 10), "campaign.result_buffer": strconv.FormatUint(config.ResultBuffer, 10), "campaign.stop_on_failure": strconv.FormatBool(config.StopOnFailure)}
	assuranceControlBudgets(limits, budgets)
	if limits.MaxDuration != nil {
		v := *limits.MaxDuration
		limits.MaxDuration = &v
	}
	expected := make([]uint64, 0, int(config.Jobs))
	for i := uint64(0); i < config.Jobs; i++ {
		expected = append(expected, config.FirstJob+i)
	}
	ledger := &assuranceLedger{maximum: assuranceEvidenceLimit(config)}
	wrappedFactory := func(id uint64) (CampaignModel[S, I, O], error) {
		if factory == nil {
			return CampaignModel[S, I, O]{}, &ModelError{"nil campaign model factory"}
		}
		model, err := factory(id)
		if err != nil {
			return model, err
		}
		original := model.Metadata
		if original != nil {
			model.Metadata = func() (Metadata, error) {
				actual, err := original()
				if err != nil {
					return actual, err
				}
				if actual != metadata {
					return actual, &ModelError{"campaign assurance executable identity mismatch"}
				}
				return actual, nil
			}
		}
		return model, nil
	}
	runner := func(id uint64, g GenerateModel[S, I, O], cfg FuzzConfig64, runLimits RunLimits, panics *campaignPanic) (report *FuzzReport64[I], err error) {
		control, e := newRunControl(runLimits)
		if e != nil {
			return nil, e
		}
		var coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error)
		if coverageFactory != nil {
			coverage, err = coverageFactory(id, g)
			if err != nil {
				return nil, panics.ownedError(err)
			}
		}
		collector, e := NewAssuranceCollectorWithLimits(manifest, metadata, coverage, observationLimits)
		if e != nil {
			return nil, e
		}
		jobBudgets := map[string]string{"seed": strconv.FormatUint(cfg.Seed, 10), "cases": strconv.FormatUint(cfg.Cases, 10), "max_steps": strconv.FormatUint(cfg.MaxSteps, 10), "max_transitions": strconv.FormatUint(cfg.MaxTransitions, 10), "mutation_percent": strconv.Itoa(cfg.MutationPercent)}
		defer func() {
			if p := recover(); p != nil {
				e := collector.Finish("failed", "Panicked", &ModelError{"job panicked; partial observations retained"})
				e.Budgets = jobBudgets
				ledger.retain(id, e)
				panic(p)
			}
			outcome, termination := "failed", "ModelError"
			if err == nil && report != nil {
				termination = report.Termination
				outcome = assuranceOutcome(termination)
			}
			finishErr := err
			if panics.seen {
				// Mirrors Rust run_campaign's catch_unwind branch: an
				// unwound job is "Panicked" with the fixed retained message.
				outcome, termination = "failed", "Panicked"
				finishErr = &ModelError{"job panicked; partial observations retained"}
				if err == nil {
					err = finishErr
				}
			}
			e := collector.Finish(outcome, termination, finishErr)
			e.Budgets = jobBudgets
			if !ledger.retain(id, e) {
				report = nil
				err = &ModelError{"assurance campaign evidence ledger limit exceeded"}
			}
		}()
		searchLimits := campaignRemaining(control)
		assuranceControlBudgets(searchLimits, jobBudgets)
		return FuzzObserved64(g, cfg, searchLimits, collector.Observer())
	}
	campaign, err := runCampaignUsing64(config, limits, wrappedFactory, sink, runner, campaignRuntime{})
	if err != nil {
		return result, err
	}
	collected := ledger.snapshot()
	merged, err := MergeAssuranceReportsWithLimits(manifest, metadata, expected, collected, observationLimits)
	if err != nil {
		return result, err
	}
	// Own the error message used by the aggregate proof.
	frozen := campaign
	if frozen.Termination.Error != nil {
		frozen.Termination.Error = &ModelError{campaignOwnedError(frozen.Termination.Error).Error()}
	}
	applyAssuranceCampaign(&merged.Report, frozen, budgets)
	merged.Report.merged.campaign = &frozen
	merged.Report.merged.campaignBudgets = maps.Clone(budgets)
	merged.Report.merged.maxEvidenceBytes = ledger.maximum
	sealAssuranceMerge(&merged.Report)
	return AssuredCampaign{Campaign: campaign, Assurance: merged}, nil
}

// AssuranceRunGuidedCampaign64 runs fresh guided feedback per job through the campaign coordinator.
func AssuranceRunGuidedCampaign64[S, I, O, K any](config CampaignConfig64, limits RunLimits, manifest AssuranceManifest, metadata Metadata, guidance GuidanceConfig64,
	factory func(uint64) (CampaignModel[S, I, O], error),
	feedbackFactory func(uint64, GenerateModel[S, I, O]) (CampaignFeedback[S, I, O, K], error),
	coverageFactory func(uint64, GenerateModel[S, I, O]) (func(S, I, Transition[S, O], []I) (AssuranceHits, error), error),
	sink func(JobReport64[I]) error,
) (AssuredCampaign, error) {
	return AssuranceRunGuidedCampaign64WithObservationLimits(config, limits, manifest, metadata, guidance, factory, feedbackFactory, coverageFactory, sink, DefaultAssuranceObservationLimits())
}

func AssuranceRunGuidedCampaign64WithObservationLimits[S, I, O, K any](config CampaignConfig64, limits RunLimits, manifest AssuranceManifest, metadata Metadata, guidance GuidanceConfig64,
	factory func(uint64) (CampaignModel[S, I, O], error),
	feedbackFactory func(uint64, GenerateModel[S, I, O]) (CampaignFeedback[S, I, O, K], error),
	coverageFactory func(uint64, GenerateModel[S, I, O]) (func(S, I, Transition[S, O], []I) (AssuranceHits, error), error),
	sink func(JobReport64[I]) error,
	observationLimits AssuranceObservationLimits,
) (AssuredCampaign, error) {
	var result AssuredCampaign
	limits = snapshotRunLimits(limits)
	if err := guidance.Validate(); err != nil {
		return result, err
	}
	if err := config.Validate(); err != nil {
		return result, err
	}
	if err := validateAssuranceAdmission(manifest, metadata, observationLimits); err != nil {
		return result, err
	}
	if manifest.Model != metadata.Name {
		return result, &ModelError{"campaign model identity mismatch"}
	}
	if err := assuranceCampaignJobLimit(config); err != nil {
		return result, err
	}
	manifest = cloneAssuranceManifest(manifest)
	budgets := map[string]string{"campaign.master_seed": strconv.FormatUint(config.MasterSeed, 10), "campaign.first_job": strconv.FormatUint(config.FirstJob, 10), "campaign.workers": strconv.FormatUint(config.Workers, 10), "campaign.result_buffer": strconv.FormatUint(config.ResultBuffer, 10), "campaign.stop_on_failure": strconv.FormatBool(config.StopOnFailure)}
	assuranceControlBudgets(limits, budgets)
	if limits.MaxDuration != nil {
		v := *limits.MaxDuration
		limits.MaxDuration = &v
	}
	expected := make([]uint64, 0, int(config.Jobs))
	for i := uint64(0); i < config.Jobs; i++ {
		expected = append(expected, config.FirstJob+i)
	}
	ledger := &assuranceLedger{maximum: assuranceEvidenceLimit(config)}
	wrappedFactory := func(id uint64) (CampaignModel[S, I, O], error) {
		if factory == nil {
			return CampaignModel[S, I, O]{}, &ModelError{"nil campaign model factory"}
		}
		model, err := factory(id)
		if err != nil {
			return model, err
		}
		original := model.Metadata
		if original != nil {
			model.Metadata = func() (Metadata, error) {
				actual, err := original()
				if err != nil {
					return actual, err
				}
				if actual != metadata {
					return actual, &ModelError{"campaign assurance executable identity mismatch"}
				}
				return actual, nil
			}
		}
		return model, nil
	}
	runner := func(id uint64, g GenerateModel[S, I, O], cfg FuzzConfig64, runLimits RunLimits, panics *campaignPanic) (report *FuzzReport64[I], err error) {
		control, e := newRunControl(runLimits)
		if e != nil {
			return nil, e
		}
		var coverage func(S, I, Transition[S, O], []I) (AssuranceHits, error)
		if coverageFactory != nil {
			coverage, err = coverageFactory(id, g)
			if err != nil {
				return nil, panics.ownedError(err)
			}
		}
		if feedbackFactory == nil {
			return nil, &ModelError{"nil campaign feedback factory"}
		}
		ownedFeedback, e := feedbackFactory(id, g)
		if e != nil {
			return nil, panics.ownedError(e)
		}
		// Close must run before the job evidence is finished so a close
		// failure is reflected in both the job result and its evidence. The
		// early defer only covers returns before the evidence defer exists.
		closed := false
		closeFeedback := func() error {
			if closed || ownedFeedback.Close == nil {
				return nil
			}
			closed = true
			return ownedFeedback.Close()
		}
		defer func() {
			if e := closeFeedback(); e != nil {
				report, err = nil, panics.ownedError(e)
			}
		}()
		feedback := campaignGuardFeedback(ownedFeedback.Feedback, panics)
		var identity FeedbackMetadata
		var identityKnown bool
		if original := feedback.Metadata; original != nil {
			feedback.Metadata = func() (FeedbackMetadata, error) {
				value, err := original()
				if err == nil {
					identity, identityKnown = value, true
				}
				return value, err
			}
		}
		collector, e := NewAssuranceCollectorWithLimits(manifest, metadata, coverage, observationLimits)
		if e != nil {
			return nil, e
		}
		jobBudgets := map[string]string{"seed": strconv.FormatUint(cfg.Seed, 10), "cases": strconv.FormatUint(cfg.Cases, 10), "max_steps": strconv.FormatUint(cfg.MaxSteps, 10), "max_transitions": strconv.FormatUint(cfg.MaxTransitions, 10), "mutation_percent": strconv.Itoa(cfg.MutationPercent)}
		jobBudgets["guidance.algorithm"] = GuidanceAlgorithm
		jobBudgets["guidance.max_corpus_entries"] = strconv.FormatUint(guidance.MaxCorpusEntries, 10)
		jobBudgets["guidance.max_corpus_inputs"] = strconv.FormatUint(guidance.MaxCorpusInputs, 10)
		jobBudgets["guidance.max_features"] = strconv.FormatUint(guidance.MaxFeatures, 10)
		jobBudgets["guidance.max_features_per_observation"] = strconv.FormatUint(guidance.MaxFeaturesPerObservation, 10)
		defer func() {
			if p := recover(); p != nil {
				_ = closeFeedback()
				e := collector.Finish("failed", "Panicked", &ModelError{"job panicked; partial observations retained"})
				e.Budgets = jobBudgets
				ledger.retain(id, e)
				panic(p)
			}
			if e := closeFeedback(); e != nil {
				report, err = nil, panics.ownedError(e)
			}
			outcome, termination := "failed", "ModelError"
			if err == nil && report != nil {
				termination = report.Termination
				outcome = assuranceOutcome(termination)
			}
			finishErr := err
			if panics.seen {
				// Mirrors Rust run_campaign's catch_unwind branch: an
				// unwound job is "Panicked" with the fixed retained message.
				outcome, termination = "failed", "Panicked"
				finishErr = &ModelError{"job panicked; partial observations retained"}
				if err == nil {
					err = finishErr
				}
			}
			e := collector.Finish(outcome, termination, finishErr)
			e.Budgets = jobBudgets
			if !ledger.retain(id, e) {
				report = nil
				err = &ModelError{"assurance campaign evidence ledger limit exceeded"}
			}
		}()
		searchLimits := campaignRemaining(control)
		assuranceControlBudgets(searchLimits, jobBudgets)
		report, err = GuidedFuzzObserved64(g, cfg, guidance, feedback, searchLimits, collector.Observer())
		if identityKnown {
			jobBudgets["guidance.feedback.name"] = identity.Name
			jobBudgets["guidance.feedback.version"] = strconv.FormatUint(uint64(identity.Version), 10)
			jobBudgets["guidance.feedback.build"] = identity.Build
		}
		return report, err
	}
	campaign, err := runCampaignUsing64(config, limits, wrappedFactory, sink, runner, campaignRuntime{})
	if err != nil {
		return result, err
	}
	collected := ledger.snapshot()
	merged, err := MergeAssuranceReportsWithLimits(manifest, metadata, expected, collected, observationLimits)
	if err != nil {
		return result, err
	}
	// Own the error message used by the aggregate proof.
	frozen := campaign
	if frozen.Termination.Error != nil {
		frozen.Termination.Error = &ModelError{campaignOwnedError(frozen.Termination.Error).Error()}
	}
	applyAssuranceCampaign(&merged.Report, frozen, budgets)
	merged.Report.merged.campaign = &frozen
	merged.Report.merged.campaignBudgets = maps.Clone(budgets)
	merged.Report.merged.maxEvidenceBytes = ledger.maximum
	sealAssuranceMerge(&merged.Report)
	return AssuredCampaign{Campaign: campaign, Assurance: merged}, nil
}

// AssurancePropertyResult and the other catalog rows are derived from retained
// observations and the declared manifest, never supplied by a coverage callback.
type AssurancePropertyResult struct {
	Property AssuranceProperty
	Status   string
	Counts   AssuranceCheckCounts
}
type AssuranceFamilyResult struct{ Name, Status, Reason string }
type AssuranceGoalResult struct {
	Goal   AssuranceGoal
	Status string
}
type AssuranceCatalog struct {
	Properties []AssurancePropertyResult
	Families   []AssuranceFamilyResult
	Goals      []AssuranceGoalResult
}

// Catalog distinguishes graph-unreachable transition goals from history goals,
// which graph deduplication alone cannot prove unreachable.
func (e AssuranceEvidence) Catalog() (AssuranceCatalog, error) {
	var out AssuranceCatalog
	assessment := e.AssessStrict()
	if len(assessment.Reasons) > 0 && strings.HasPrefix(assessment.Reasons[0], "inconsistent assurance report:") {
		return out, &ModelError{assessment.Reasons[0]}
	}
	for _, p := range e.Manifest.Properties {
		c := e.Properties[p]
		status := "missing"
		switch {
		case c.Failed > 0:
			status = "failed"
		case c.Skipped > 0:
			status = "skipped"
		case c.Missing > 0:
			status = "missing"
		case c.Passed > 0:
			status = "passed"
		}
		out.Properties = append(out.Properties, AssurancePropertyResult{p, status, c})
	}
	slices.SortFunc(out.Properties, func(a, b AssurancePropertyResult) int {
		if n := strings.Compare(a.Property.ID, b.Property.ID); n != 0 {
			return n
		}
		return strings.Compare(a.Property.Phase, b.Property.Phase)
	})
	for _, f := range e.Manifest.Families {
		status := "missing"
		if f.Excluded {
			status = "excluded"
			if e.Families[f.Name] {
				status = "exclusion_violated"
			}
		} else if e.Families[f.Name] {
			status = "observed"
		}
		out.Families = append(out.Families, AssuranceFamilyResult{f.Name, status, f.Reason})
	}
	slices.SortFunc(out.Families, func(a, b AssuranceFamilyResult) int { return strings.Compare(a.Name, b.Name) })
	for _, g := range e.Manifest.Goals {
		status := "not_reached_before_stop"
		if e.Goals[g.ID] {
			status = "observed"
		} else if e.Outcome == "graph_exhausted" && len(e.Errors) == 0 && g.Scope == "transition" {
			status = "unreachable_under_declared_model"
		}
		out.Goals = append(out.Goals, AssuranceGoalResult{g, status})
	}
	slices.SortFunc(out.Goals, func(a, b AssuranceGoalResult) int { return strings.Compare(a.Goal.ID, b.Goal.ID) })
	return out, nil
}
