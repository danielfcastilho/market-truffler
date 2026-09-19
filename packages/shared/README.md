# packages/shared

Reserved boundary for code shared across multiple apps (for example, types
or constants shared between `apps/api` and `apps/web`, or later between
Sniffer/Warhog services).

## Status

Empty in the foundation milestone. There is currently only one frontend and
one backend with no meaningful overlap worth extracting — adding a shared
package now would be premature abstraction. This directory exists so the
boundary is easy to introduce later without restructuring the monorepo.
