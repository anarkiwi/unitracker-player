; The playroutine. init zeroes the SID, sets each stream at its start and installs a raster
; IRQ; each IRQ plays one tick: per stream, a run's tick adds its delta to the stream's value
; and stores it, and fetch reads the next run when one ends.

        .include "song.inc"

        .section .init,"ax",@progbits
        .globl init
init:   sei
        jsr init_state
        lda #0x7f
        sta 0xdc0d
        lda 0xdc0d
        lda #mos16lo(isr)
        sta 0x0314
        lda #mos16hi(isr)
        sta 0x0315
        lda #RASTER
        sta 0xd012
        lda 0xd011
        and #0x7f
        sta 0xd011
        lda #1
        sta 0xd01a
        sta 0xd019
        cli
        rts

        .section .text.player,"ax",@progbits
        .globl init_state
init_state:
        cld
        ldx #24
        lda #0
1:      sta 0xd400,x
        dex
        bpl 1b
        ldx #STREAMS-1
2:      lda #1
        sta cnt,x
        lda #0
        sta dlo,x
        sta dhi,x
        sta vlo,x
        sta vhi,x
        txa
        asl
        tay
        lda start,y
        sta ipl,x
        lda start+1,y
        sta iph,x
        lda stack_base,x
        sta depth,x
        dex
        bpl 2b
        rts

isr:    cld
        lda #1
        sta 0xd019
        jsr tick
        jmp 0xea81

; Reads stream X's next run: 0nnnnnnn holds n ticks; 10nnnnnn d adds d (sign-extended) n
; ticks; 110nnnnn lo hi adds a word n ticks; e0 calls, e1 jumps to, the address after it;
; e2 returns; e3 holds forever.
        .globl fetch
fetch:  lda ipl,x
        sta ptr
        lda iph,x
        sta ptr+1
op:     ldy #0
        lda (ptr),y
        bmi delta
        sta cnt,x
        lda #0
        sta dlo,x
        sta dhi,x
        lda #1
        bne advance
delta:  cmp #0xe0
        bcs control
        cmp #0xc0
        bcs word
        and #0x3f
        sta cnt,x
        iny
        lda (ptr),y
        sta dlo,x
        asl
        lda #0
        adc #0xff
        eor #0xff
        sta dhi,x
        lda #2
        bne advance
word:   and #0x1f
        sta cnt,x
        iny
        lda (ptr),y
        sta dlo,x
        iny
        lda (ptr),y
        sta dhi,x
        lda #3
advance:
        clc
        adc ptr
        sta ipl,x
        lda #0
        adc ptr+1
        sta iph,x
        rts
control:
        cmp #0xe2
        beq return
        bcs stop
        cmp #0xe1
        beq jump
        ldy depth,x
        clc
        lda ptr
        adc #3
        sta stack_lo,y
        lda ptr+1
        adc #0
        sta stack_hi,y
        inc depth,x
jump:   ldy #1
        lda (ptr),y
        pha
        iny
        lda (ptr),y
        sta ptr+1
        pla
        sta ptr
        jmp op
return: dec depth,x
        ldy depth,x
        lda stack_lo,y
        sta ptr
        lda stack_hi,y
        sta ptr+1
        jmp op
stop:   lda #0xff
        sta cnt,x
        lda #0
        sta dlo,x
        sta dhi,x
        rts
