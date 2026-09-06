import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter, Navigate } from "react-router-dom";

import "./index.css";
import { AuthProvider } from "./auth";
import { Shell } from "./components/Shell";
import { LoginPage } from "./pages/LoginPage";
import { OfficerQueuePage } from "./pages/OfficerQueuePage";
import { CasePage } from "./pages/CasePage";

/** Retries are off by default. A failed read on a decision screen should say
 *  so rather than quietly try again: an officer who cannot see the evidence
 *  needs to know that, not to wait. */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15_000 },
  },
});

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/officer" replace /> },
      { path: "officer", element: <OfficerQueuePage /> },
      { path: "officer/cases/:caseId", element: <CasePage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
