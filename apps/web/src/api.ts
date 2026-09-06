import { useAuth } from "./auth";

/** Every response carries X-Trace-Id (docs/08 preamble). The header is
 *  surfaced in the shell so a reader can quote it when something looks
 *  wrong, rather than describing the symptom. */
export const TRACE_HEADER = "x-trace-id";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown = null,
  ) {
    super(message);
  }
}

type Options = { method?: string; body?: unknown };

export function createClient(
  token: string | null,
  noteTraceId: (id: string | null) => void,
) {
  return async function request<T>(path: string, options: Options = {}): Promise<T> {
    const response = await fetch(path, {
      method: options.method ?? "GET",
      headers: {
        "content-type": "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });

    noteTraceId(response.headers.get(TRACE_HEADER));

    if (!response.ok) {
      // The platform's error envelope is {error:{code,message,details}}
      // (docs/08 preamble). A body that is not one is still an error, so it
      // is reported rather than swallowed.
      let code = "UNKNOWN";
      let message = `${response.status} ${response.statusText}`;
      let details: unknown = null;
      try {
        const body = (await response.json()) as {
          error?: { code?: string; message?: string; details?: unknown };
        };
        code = body.error?.code ?? code;
        message = body.error?.message ?? message;
        details = body.error?.details ?? null;
      } catch {
        /* not an envelope; the status line stands */
      }
      throw new ApiError(response.status, code, message, details);
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  };
}

export function useApi() {
  const { session, noteTraceId } = useAuth();
  return createClient(session?.token ?? null, noteTraceId);
}

/** Fetch a binary response and hand back an object URL for it.
 *
 *  An <img src="/api/..."> sends no Authorization header, so every image
 *  behind the gateway would 403. Putting the token in the query string would
 *  work and is exactly the wrong fix: it would land in access logs and
 *  referrers. The bytes are fetched with the header and wrapped in a blob URL
 *  instead, which the caller must revoke. */
export function createBlobClient(token: string | null) {
  return async function fetchBlob(path: string): Promise<string> {
    const response = await fetch(path, {
      headers: token ? { authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        "UNAVAILABLE",
        `${response.status} ${response.statusText}`,
      );
    }
    return URL.createObjectURL(await response.blob());
  };
}

export function useBlobApi() {
  const { session } = useAuth();
  return createBlobClient(session?.token ?? null);
}
