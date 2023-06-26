horizon = 32
n_obs_steps = 6
future_action_len = horizon - n_obs_steps
workdir = "rgbd"
agent_cfg = dict(
    type="KeyDiffAgent",
    # train_diff_model=True,
    batch_size=256,
    action_seq_len=horizon,
    visual_nn_cfg=dict(
        type="MultiImageObsEncoder", 
        shape_meta=dict(
            obs=dict(
                base_camera_rgbd=dict(
                    type="rgbd",
                    shape="image_size",
                    channel=4
                ),
                hand_camera_rgbd=dict(
                    type="rgbd",
                    shape="image_size",
                    channel=4
                ),
                state=dict(
                    type="low_dim",
                    shape="agent_shape"
                )
            )
        ),
    ),
    actor_cfg=dict(
        type="ContDiffActor",
    ),
    n_obs_steps=n_obs_steps, # n_obs_steps - 1 is the history length of the action, n_obs_steps is the history length of the observation
    obs_as_global_cond=True,
    fix_obs_stepd=True,
    action_visible=True,
    optim_cfg=dict(type="Adam", lr=3e-4),
    diff_nn_cfg=dict(
        type="ConditionalUnet1D",
        input_dim="action_shape",
        local_cond_dim=None,
        global_cond_dim=None,
        diffusion_step_embed_dim=256,
        down_dims=[256,512,1024],
        kernel_size=3,
        n_groups=8,
        cond_predict_scale=False,
    ),
    keyframe_model_cfg=dict(
        state_dim="agent_shape",
        action_dim="action_shape",
        model_type="s+a",
        block_size=64,
        n_layer=4,
        n_head=8, 
        n_embd=128,
        max_timestep=200,
        hist_horizon=n_obs_steps,
        optim_cfg=dict(
            init_lr=5e-4,
            weight_decay=0,
            beta1=0.9,
            beta2=0.95,
        ),
    ),
)

# env_cfg = dict(
#     type="gym",
#     env_name="PickCube-v0",
#     unwrapped=False,
#     history_len=n_obs_steps,
#     obs_mode="rgbd",
#     control_mode="pd_joint_pos"
# )


replay_cfg = dict(
    type="ReplayMemory",
    sampling_cfg=dict(
        type="TStepTransition",
        horizon=horizon,
        future_action_len=future_action_len,
    ),
    capacity=-1,
    num_samples=-1,
    keys=["obs", "actions", "dones", "episode_dones", "keyframes", "keytime_differences", "keyframe_masks", "timesteps"],
    buffer_filenames=[
        "SOME_DEMO_FILE",
    ],
    num_procs=32,
    synchronized=False,
)

train_cfg = dict(
    on_policy=False,
    total_steps=500000,
    warm_steps=0,
    n_steps=0,
    n_updates=500,
    n_eval=10000,
    n_checkpoint=10000,
)

eval_cfg = dict(
    type="OfflineDiffusionEvaluation",
    num=10,
    num_procs=1,
    use_hidden_state=False,
    save_traj=False,
    use_log=False,
)