/* The explicit library entry point: pyscriptc_init().

A static archive has no constructor to hang initialization on. ld64 (and every
other static linker) pulls an archive member only when it resolves an
undefined symbol, so a member whose only contribution is an initializer is
dropped without a link error: the app links, runs, and the initialization
never happened.

So a library build has one explicit init call. `pyscriptc_init()` prepares
the runtime state the emitted module needs and is idempotent, so a host may
call it from more than one place. The emitted module's own entry
(`pyscriptc_main`, defined in the .ll of a `--emit lib` build) calls it
before anything else runs.

The binary build does not use it: `main` runs the module's globals directly,
the way it always has. Both paths share the same runtime state, so a program
built one way and a library built the other behave the same.
*/

#include "ps_rt.h"

#include <stddef.h>

/* The runtime state a library build needs: an empty argv list. The exception
   table is a compile-time constant and the user exception classes register
   from the emitted module entry, so there is nothing else to seed here. */

__attribute__((visibility("default"))) int pyscriptc_init(void);

void ps_argv_init(int32_t argc, char **argv);

static int ps_init_done;

int pyscriptc_init(void) {
    if (ps_init_done) {
        return 0;
    }
    ps_init_done = 1;
    /* An empty argv list: a library has no process arguments, and a program
       that reads sys.argv in a mobile host gets an empty list, not a
       dangling pointer. */
    ps_argv_init(0, NULL);
    return 0;
}
