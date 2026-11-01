//! The call machine of unitracker-player: a compiled document played to SID register state, one
//! frame at a time, with no allocation after construction.

pub mod expr;
pub mod machine;
pub mod player;
pub mod sequencer;
pub mod spec;

pub use machine::Machine;
pub use player::Player;
pub use spec::Spec;
