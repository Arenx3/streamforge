import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// A loaded CI machine can take longer than the 1 s default to render a response.
configure({ asyncUtilTimeout: 4000 });

afterEach(cleanup);
