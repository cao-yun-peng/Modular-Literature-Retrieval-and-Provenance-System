import { useNavigate, useSearch } from "@tanstack/react-router";
export type Search = {
  run?: string;
  chunk?: string;
  view?: string;
  doc?: string;
};
export function useNav() {
  const navigate = useNavigate();
  return (path: string, search: Search = {}) =>
    navigate({ to: path, search } as never);
}
export function usePageSearch() {
  return useSearch({ strict: false }) as Search;
}
