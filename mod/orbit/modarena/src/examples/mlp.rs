// A neural network in the registry — the smallest honest one.
//
// Two inputs, two tanh hidden units, one sigmoid output, computing XOR. The
// weights are set by hand rather than trained, because the point of this
// example is not the model: it is that a model is just another module here.
// You store it by the hash of its bytes, you introspect its exports, and you
// run it in a browser tab with no runtime to install and no server to trust
// with the input.
//
// Everything a bigger model needs is already in this shape — weights in the
// module, an exported entry point, floats in and floats out. The difference
// between this and something worth running is a few megabytes of parameters
// and a matmul.

include!("abi.rs");

/// Hidden layer: weights, then bias, per unit.
const W1: [[f32; 3]; 2] = [
    [2.0, 2.0, -1.0],   // fires when either input is on
    [-2.0, -2.0, 3.0],  // fires unless both are on
];
/// Output layer: one weight per hidden unit, then bias.
const W2: [f32; 3] = [6.0, 6.0, -5.25];

fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x).exp())
}

fn forward(a: f32, b: f32) -> f32 {
    let h: [f32; 2] = [
        (W1[0][0] * a + W1[0][1] * b + W1[0][2]).tanh(),
        (W1[1][0] * a + W1[1][1] * b + W1[1][2]).tanh(),
    ];
    sigmoid(W2[0] * h[0] + W2[1] * h[1] + W2[2])
}

/// The headline entry point: two floats in, one float out. Callable straight
/// from the console's RUN box, no marshalling.
#[no_mangle]
pub extern "C" fn predict(a: f32, b: f32) -> f32 {
    forward(a, b)
}

/// The same thing rounded to a decision, for callers that want an answer
/// rather than a confidence.
#[no_mangle]
pub extern "C" fn classify(a: f32, b: f32) -> i32 {
    (forward(a, b) >= 0.5) as i32
}

/// Every input and what the model says about it — a self-test you can read.
#[no_mangle]
pub extern "C" fn evaluate() -> i64 {
    let mut rows = Vec::new();
    let mut correct = 0;
    for (a, b) in [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)] {
        let p: f32 = forward(a, b);
        let want = ((a as i32) ^ (b as i32)) as f32;
        if (p >= 0.5) == (want >= 0.5) {
            correct += 1;
        }
        rows.push(format!(
            "{{\"in\":[{a},{b}],\"out\":{:.4},\"want\":{want}}}",
            p
        ));
    }
    ret(format!(
        "{{\"task\":\"xor\",\"shape\":[2,2,1],\"parameters\":9,\"correct\":{correct},\
          \"of\":4,\"rows\":[{}]}}",
        rows.join(",")
    ))
}

/// The parameters, so a stored model can be read as well as run.
#[no_mangle]
pub extern "C" fn weights() -> i64 {
    ret(format!(
        "{{\"hidden\":[[{},{},{}],[{},{},{}]],\"output\":[{},{},{}]}}",
        W1[0][0], W1[0][1], W1[0][2],
        W1[1][0], W1[1][1], W1[1][2],
        W2[0], W2[1], W2[2],
    ))
}
