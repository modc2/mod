package main

import "github.com/consensys/gnark/frontend"

// FactorsGnark: prove you know A and B with A * B == C. C is public, A and B are
// secret (gnark's default), and neither may be 1 (otherwise "1 * C" proves
// nothing).
type Circuit struct {
	A frontend.Variable `gnark:"a"`
	B frontend.Variable `gnark:"b"`
	C frontend.Variable `gnark:"c,public"`
}

func (c *Circuit) Define(api frontend.API) error {
	api.AssertIsDifferent(c.A, 1)
	api.AssertIsDifferent(c.B, 1)
	api.AssertIsEqual(api.Mul(c.A, c.B), c.C)
	return nil
}
