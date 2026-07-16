import { useQuery } from "@tanstack/react-query";
import { request } from "@/api/client";

export interface ComponentStatus {
  name: string;
  healthy: boolean;
}

export interface HealthResponse {
  status: string;
  app_name: string;
  environment: string;
  components: ComponentStatus[];
}

const HEALTH_ENDPOINT = "/api/v1/health";

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => request<HealthResponse>(HEALTH_ENDPOINT),
    refetchInterval: 15_000,
  });
}
