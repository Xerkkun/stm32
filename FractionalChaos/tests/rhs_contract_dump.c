#include "fractional_chaos.h"

#include <inttypes.h>
#include <stdio.h>

/*
 * Nominal, boundary-like, and reproducibly frozen pseudo-random states.  The
 * latter were sampled once from a fixed seed and are now literal test inputs,
 * so changes in a host RNG cannot move the C/Python equation contract.
 */
static const fc_vec3f_t FC_RHS_TEST_STATES[] = {
    {{0.0f, 0.0f, 0.0f}},
    {{1.0f, -2.0f, 3.0f}},
    {{-4.0f, 4.0f, -1.5f}},
    {{0.125f, -0.25f, 0.5f}},
    {{2.781347f, -3.114529f, 1.908217f}},
    {{-0.774631f, 2.665104f, -3.552871f}},
    {{3.408926f, 0.317445f, -2.109763f}},
    {{-2.936118f, -1.428507f, 3.771204f}},
    {{1.562903f, 3.046821f, 0.884392f}},
    {{-3.219774f, 1.197536f, 2.438615f}},
    {{0.493187f, -3.684250f, -0.617902f}},
    {{2.104568f, 0.958731f, -3.006445f}},
};

int main(void)
{
    uint32_t system;
    uint32_t case_index;

    puts("system,case,x,y,z,dx,dy,dz,status");
    for (system = 0u; system < FC_SYSTEM_COUNT; ++system) {
        const fc_manifest_t *manifest =
            fc_manifest((fc_system_t)system);

        if (manifest == NULL) {
            return 1;
        }
        for (case_index = 0u;
             case_index <
             (uint32_t)(sizeof(FC_RHS_TEST_STATES) /
                        sizeof(FC_RHS_TEST_STATES[0]));
             ++case_index) {
            fc_vec3f_t derivative = {{0.0f, 0.0f, 0.0f}};
            const fc_status_t status = fc_rhs(
                (fc_system_t)system,
                manifest->parameters,
                &FC_RHS_TEST_STATES[case_index],
                &derivative);

            printf(
                "%" PRIu32 ",%" PRIu32
                ",%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%d\n",
                system,
                case_index,
                (double)FC_RHS_TEST_STATES[case_index].v[0],
                (double)FC_RHS_TEST_STATES[case_index].v[1],
                (double)FC_RHS_TEST_STATES[case_index].v[2],
                (double)derivative.v[0],
                (double)derivative.v[1],
                (double)derivative.v[2],
                (int)status);
            if (status != FC_OK) {
                return 1;
            }
        }
    }
    return 0;
}
