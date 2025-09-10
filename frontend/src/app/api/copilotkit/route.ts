import {
  CopilotRuntime,
  copilotRuntimeNextJSAppRouterEndpoint,
  GoogleGenerativeAIAdapter
} from '@copilotkit/runtime';
import { NextRequest } from 'next/server';
import { HttpAgent } from "@ag-ui/client";


const crewaiAgent = new HttpAgent({
  // url: "http://0.0.0.0:8000/crewai-agent",
  url: process.env.NEXT_PUBLIC_CREWAI_URL || "http://0.0.0.0:8000/crewai-agent",
});
const serviceAdapter = new GoogleGenerativeAIAdapter({
  apiKey: process.env.GOOGLE_GENERATIVE_AI_API_KEY,
  model: "gemini-2.0-flash-exp"
})
const runtime = new CopilotRuntime({
  agents: {
    // @ts-ignore
    crewaiAgent : crewaiAgent
  },
});
// const runtime = new CopilotRuntime()
export const POST = async (req: NextRequest) => {
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter,
    endpoint: '/api/copilotkit',
  });

  return handleRequest(req);
};
