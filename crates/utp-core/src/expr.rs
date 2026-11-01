//! Modulator expressions as postfix code over `v`, `phi`, `N` and `n`, evaluated on 64-bit
//! wrapping integers with Python's floor division and modulo, and 0 for a division by 0 and a
//! shift by a negative count.

pub const DEPTH: usize = 32;

pub mod code {
    pub const CONST: i64 = 0;
    pub const V: i64 = 1;
    pub const PHI: i64 = 2;
    pub const NOTE: i64 = 3;
    pub const EVENT: i64 = 4;
    pub const ADD: i64 = 5;
    pub const SUB: i64 = 6;
    pub const MUL: i64 = 7;
    pub const DIV: i64 = 8;
    pub const MOD: i64 = 9;
    pub const OR: i64 = 10;
    pub const XOR: i64 = 11;
    pub const AND: i64 = 12;
    pub const SHL: i64 = 13;
    pub const SHR: i64 = 14;
    pub const NEG: i64 = 15;
    pub const INV: i64 = 16;
    pub const NOT: i64 = 17;
    pub const EQ: i64 = 18;
    pub const NE: i64 = 19;
    pub const LT: i64 = 20;
    pub const LE: i64 = 21;
    pub const GT: i64 = 22;
    pub const GE: i64 = 23;
    pub const ALL: i64 = 24;
    pub const ANY: i64 = 25;
    pub const ABS: i64 = 26;
    pub const MIN: i64 = 27;
    pub const MAX: i64 = 28;
    pub const F: i64 = 29;
    pub const LO: i64 = 30;
    pub const HI: i64 = 31;
    pub const U8: i64 = 32;
    pub const U16: i64 = 33;
    pub const SX: i64 = 34;
    pub const ITE: i64 = 35;
    pub const TABLE: i64 = 36;
}

/// The note table `F` and the named tables an expression reads, 0 outside them.
pub struct Tables {
    pub f_low: i64,
    pub f: Vec<i64>,
    pub named: Vec<Vec<i64>>,
}

impl Tables {
    #[inline]
    pub fn freq(&self, k: i64) -> i64 {
        at(&self.f, k - self.f_low)
    }
}

#[inline]
fn at(values: &[i64], k: i64) -> i64 {
    if k >= 0 && (k as usize) < values.len() {
        values[k as usize]
    } else {
        0
    }
}

#[inline]
pub fn floor_div(a: i64, b: i64) -> i64 {
    if b == 0 {
        return 0;
    }
    let q = a.wrapping_div(b);
    if a.wrapping_rem(b) != 0 && ((a < 0) != (b < 0)) {
        q - 1
    } else {
        q
    }
}

#[inline]
pub fn floor_mod(a: i64, b: i64) -> i64 {
    if b == 0 {
        return 0;
    }
    let r = a.wrapping_rem(b);
    if r != 0 && ((r < 0) != (b < 0)) {
        r + b
    } else {
        r
    }
}

#[inline]
fn shl(a: i64, b: i64) -> i64 {
    if (0..64).contains(&b) {
        a << b
    } else {
        0
    }
}

#[inline]
fn shr(a: i64, b: i64) -> i64 {
    match b {
        0..=63 => a >> b,
        64.. => a >> 63,
        _ => 0,
    }
}

/// The code's value at `(v, phi, n_sounding, n_event)`; the code is checked by [`check`].
pub fn eval(code: &[i64], tables: &Tables, v: i64, phi: i64, note: i64, event: i64) -> i64 {
    use code::*;
    let mut stack = [0i64; DEPTH];
    let mut top = 0usize;
    let mut pc = 0usize;
    macro_rules! binary {
        ($f:expr) => {{
            top -= 1;
            stack[top - 1] = $f(stack[top - 1], stack[top]);
        }};
    }
    while pc < code.len() {
        let op = code[pc];
        pc += 1;
        match op {
            CONST => {
                stack[top] = code[pc];
                pc += 1;
                top += 1;
            }
            V => {
                stack[top] = v;
                top += 1;
            }
            PHI => {
                stack[top] = phi;
                top += 1;
            }
            NOTE => {
                stack[top] = note;
                top += 1;
            }
            EVENT => {
                stack[top] = event;
                top += 1;
            }
            ADD => binary!(i64::wrapping_add),
            SUB => binary!(i64::wrapping_sub),
            MUL => binary!(i64::wrapping_mul),
            DIV => binary!(floor_div),
            MOD => binary!(floor_mod),
            OR => binary!(|a, b| a | b),
            XOR => binary!(|a, b| a ^ b),
            AND => binary!(|a, b| a & b),
            SHL => binary!(shl),
            SHR => binary!(shr),
            NEG => stack[top - 1] = stack[top - 1].wrapping_neg(),
            INV => stack[top - 1] = !stack[top - 1],
            NOT => stack[top - 1] = (stack[top - 1] == 0) as i64,
            EQ => binary!(|a, b| (a == b) as i64),
            NE => binary!(|a, b| (a != b) as i64),
            LT => binary!(|a, b| (a < b) as i64),
            LE => binary!(|a, b| (a <= b) as i64),
            GT => binary!(|a, b| (a > b) as i64),
            GE => binary!(|a, b| (a >= b) as i64),
            ALL | ANY => {
                let n = code[pc] as usize;
                pc += 1;
                let args = &stack[top - n..top];
                let hit = if op == ALL {
                    args.iter().all(|&x| x != 0)
                } else {
                    args.iter().any(|&x| x != 0)
                };
                top -= n;
                stack[top] = hit as i64;
                top += 1;
            }
            ABS => stack[top - 1] = stack[top - 1].wrapping_abs(),
            MIN => binary!(i64::min),
            MAX => binary!(i64::max),
            F => stack[top - 1] = tables.freq(stack[top - 1]),
            LO | U8 => stack[top - 1] &= 255,
            HI => stack[top - 1] = (stack[top - 1] >> 8) & 255,
            U16 => stack[top - 1] &= 65535,
            SX => {
                let b = stack[top - 1] & 255;
                stack[top - 1] = b | (65280 * (b >> 7));
            }
            ITE => {
                top -= 2;
                stack[top - 1] = if stack[top - 1] != 0 {
                    stack[top]
                } else {
                    stack[top + 1]
                };
            }
            TABLE => {
                let t = code[pc] as usize;
                pc += 1;
                stack[top - 1] = at(&tables.named[t], stack[top - 1]);
            }
            _ => unreachable!(),
        }
    }
    stack[0]
}

/// Whether `code` is well formed: known operators, operands present, a stack that never
/// under- or overflows and leaves one value, tables in range.
pub fn check(code: &[i64], tables: usize) -> Result<(), String> {
    use code::*;
    let (mut pc, mut depth) = (0usize, 0i64);
    while pc < code.len() {
        let op = code[pc];
        pc += 1;
        let (pops, pushes, operand) = match op {
            CONST => (0, 1, 1),
            V | PHI | NOTE | EVENT => (0, 1, 0),
            ADD..=SHR | EQ..=GE | MIN | MAX => (2, 1, 0),
            NEG | INV | NOT | ABS | F | LO | HI | U8 | U16 | SX => (1, 1, 0),
            ALL | ANY => (*code.get(pc).ok_or("missing count")?, 1, 1),
            ITE => (3, 1, 0),
            TABLE => (1, 1, 1),
            _ => return Err(format!("unknown operator {op}")),
        };
        if op == TABLE && !code.get(pc).is_some_and(|&t| (t as usize) < tables) {
            return Err("unknown table".into());
        }
        pc += operand;
        if pc > code.len() || depth < pops {
            return Err("malformed code".into());
        }
        depth += pushes - pops;
        if depth as usize > DEPTH {
            return Err("expression too deep".into());
        }
    }
    if depth == 1 {
        Ok(())
    } else {
        Err("expression leaves no one value".into())
    }
}

#[cfg(test)]
mod tests {
    use super::code::*;
    use super::*;

    fn tables() -> Tables {
        Tables {
            f_low: -2,
            f: vec![7, 8, 9, 10],
            named: vec![vec![5, 6, 7]],
        }
    }

    fn run(code: &[i64]) -> i64 {
        check(code, 1).unwrap();
        eval(code, &tables(), 100, 3, 4, 5)
    }

    #[test]
    fn python_floor_division_and_modulo() {
        assert_eq!(floor_div(-7, 2), -4);
        assert_eq!(floor_div(7, -2), -4);
        assert_eq!(floor_div(-7, -2), 3);
        assert_eq!(floor_mod(-7, 2), 1);
        assert_eq!(floor_mod(7, -2), -1);
        assert_eq!(floor_div(5, 0), 0);
        assert_eq!(floor_mod(5, 0), 0);
        assert_eq!(floor_div(i64::MIN, -1), i64::MIN);
    }

    #[test]
    fn shifts_by_negative_counts_are_zero() {
        assert_eq!(run(&[CONST, 8, CONST, -1, SHL]), 0);
        assert_eq!(run(&[CONST, 8, CONST, -1, SHR]), 0);
        assert_eq!(run(&[CONST, 8, CONST, 2, SHL]), 32);
        assert_eq!(run(&[CONST, -8, CONST, 70, SHR]), -1);
    }

    #[test]
    fn names_tables_and_wraps() {
        assert_eq!(run(&[V, PHI, ADD, NOTE, MUL]), 412);
        assert_eq!(run(&[CONST, 0, F]), 9);
        assert_eq!(run(&[CONST, 9, F]), 0);
        assert_eq!(run(&[CONST, 2, TABLE, 0]), 7);
        assert_eq!(run(&[CONST, 3, TABLE, 0]), 0);
        assert_eq!(run(&[CONST, 0x1FF, SX]), 0xFFFF);
        assert_eq!(run(&[CONST, 0x1234, HI]), 0x12);
        assert_eq!(run(&[CONST, 0x1234, LO]), 0x34);
    }

    #[test]
    fn logic_and_conditionals() {
        assert_eq!(run(&[CONST, 2, CONST, 0, ALL, 2]), 0);
        assert_eq!(run(&[CONST, 2, CONST, 0, ANY, 2]), 1);
        assert_eq!(run(&[CONST, 1, CONST, 11, CONST, 22, ITE]), 11);
        assert_eq!(run(&[CONST, 0, CONST, 11, CONST, 22, ITE]), 22);
        assert_eq!(run(&[CONST, 0, NOT]), 1);
        assert_eq!(run(&[V, CONST, 100, EQ]), 1);
    }

    #[test]
    fn malformed_code_is_refused() {
        assert!(check(&[ADD], 0).is_err());
        assert!(check(&[CONST, 1, CONST, 2], 0).is_err());
        assert!(check(&[99], 0).is_err());
        assert!(check(&[CONST, 1, TABLE, 0], 0).is_err());
        assert!(check(&[CONST], 0).is_err());
    }
}
