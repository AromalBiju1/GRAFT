export async function submitQuery(query) {
  let response;
  try {
    response = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
  } catch {
    throw new Error("Could not reach the API. Check your connection and that the backend is running, then try again.");
  }

  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(response.ok ? "The API returned an invalid response. Please try again." : `Request failed (HTTP ${response.status}). Please try again.`);
  }
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail
      : Array.isArray(data?.detail) ? data.detail.map((item) => item?.msg).filter(Boolean).join("; ")
      : data?.error?.message;
    throw new Error(typeof detail === "string" && detail ? detail : `Request failed (HTTP ${response.status}). Please try again.`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("The API returned an invalid response. Please try again.");
  }
  return data;
}
