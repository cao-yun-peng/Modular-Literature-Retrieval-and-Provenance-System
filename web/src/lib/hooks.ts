import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, terminal, type Run, type Event } from "./api";
export function useRun(id?: string) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["run", id],
    queryFn: () => api<Run>(`/runs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (terminal(q.state.data?.status) ? false : 3000),
  });
  useEffect(() => {
    if (!id || terminal(query.data?.status)) return;
    const stream = new EventSource(`/api/v1/runs/${id}/events`);
    const update = () => {
      void client.invalidateQueries({ queryKey: ["run", id] });
      void client.invalidateQueries({ queryKey: ["events", id] });
      void client.invalidateQueries({ queryKey: ["result", id] });
    };
    stream.addEventListener("progress", update);
    stream.addEventListener("done", () => {
      update();
      void client.invalidateQueries({ queryKey: ["documents"] });
      void client.invalidateQueries({ queryKey: ["document"] });
      void client.invalidateQueries({ queryKey: ["chunks", id] });
      void client.invalidateQueries({ queryKey: ["stats", id] });
      stream.close();
    });
    return () => stream.close();
  }, [id, query.data?.status, client]);
  return query;
}
export function useEvents(id?: string) {
  return useQuery({
    queryKey: ["events", id],
    queryFn: () => api<Event[]>(`/runs/${id}/event-history`),
    enabled: !!id,
  });
}
