#include "ml_reference.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>

int main(void)
{
    double samples[400], features[101], scores[4];
    for (unsigned i = 0; i < 400; ++i) {
        const double t = (double)i / 400.0;
        samples[i] = 0.04 * sin(2.0 * 3.14159265358979323846 * 68.0 * t)
                   + 0.01 * cos(2.0 * 3.14159265358979323846 * 136.0 * t);
    }
    assert(ml_reference_init());
    assert(ml_reference_extract(samples, 4114.5, features));
    const int label = ml_reference_predict(features, scores);
    assert(label >= 0 && label < 4);
    for (unsigned i = 0; i < 101; ++i) assert(isfinite(features[i]));
    for (unsigned i = 0; i < 4; ++i) assert(isfinite(scores[i]));
    puts("generated 101-feature C model smoke check passed");
    return 0;
}
