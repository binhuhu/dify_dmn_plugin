// L1 ESM calculator runs in the caller's trusted environment, never inside the DMN engine.
// This synthetic calculator is illustrative, not a verified production fact definition.
export function calculateAvailabilityFact(query) {
  const base = {
    calculator_id: "synthetic.availability_fact.v1",
    order_id: query.outputs?.order_id ?? null,
  };
  if (query.status !== "SUCCEEDED" || query.outcome !== "FOUND")
    return { ...base, state: "UNKNOWN", reason_code: "QUERY_NOT_CONFIRMED" };
  const value = query.outputs.availability_state;
  if (value === "CONFIRMED_ABSENT")
    return { ...base, state: "ABSENT", reason_code: null };
  if (value === "CONFIRMED_PRESENT")
    return { ...base, state: "PRESENT", reason_code: null };
  return {
    ...base,
    state: "UNKNOWN",
    reason_code: "SOURCE_AVAILABILITY_UNKNOWN",
  };
}
