import { useQueries } from "@tanstack/react-query";

import { useApi } from "../api";

/** Who a membership number belongs to.
 *
 *  The ledger records a membership number, which is right: a name changes and
 *  an identifier does not. But a queue of rows reading M-000028 through
 *  M-000038 is a queue nobody can hold in their head, and an officer talking
 *  to a colleague says "the Carter application", not "the M-000028 one".
 *
 *  So the number stays the identity and the name is fetched beside it. The
 *  lookup never blocks a row from rendering: a name that has not arrived
 *  leaves the number showing, which is what the row said before and is never
 *  wrong. */
export function useMemberNames(ids: (string | null | undefined)[]): Map<string, string> {
  const request = useApi();
  // Only real membership numbers. A masked reference like «MEMBER_1» is what
  // the council was shown and has no name behind it to find.
  const wanted = [...new Set(ids.filter((id): id is string => !!id && /^M-/.test(id)))];

  const results = useQueries({
    queries: wanted.map((id) => ({
      queryKey: ["member-name", id],
      // A name does not change during a demonstration, and a failed lookup is
      // not worth retrying: the number is already on the screen.
      staleTime: Infinity,
      retry: false,
      queryFn: () =>
        request<{ member_id: string; name_token?: string }>(
          `/api/core_stub/core/members/${encodeURIComponent(id)}`,
        ),
    })),
  });

  const named = new Map<string, string>();
  results.forEach((result, index) => {
    const name = result.data?.name_token;
    if (name) named.set(wanted[index], name);
  });
  return named;
}
